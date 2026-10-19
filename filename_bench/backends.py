"""실제 모델과 통신하는 부분입니다. A=Transformers, B=llama-server, Mock=동작 확인."""
from __future__ import annotations

import base64
import io
import json
import os
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from .common import BenchError, file_hash


def classify_error(message):
    low = message.lower()
    if any(s in low for s in ["out of memory", "cuda out", "failed to allocate", "cuda malloc"]):
        return "oom"
    if any(s in low for s in ["context size", "context length", "too many tokens", "exceeds the available"]):
        return "input_too_long"
    return "backend_error"


class MockBackend:
    def __init__(self, cfg):
        self.cfg, self.pid = cfg, None
        self.info = {"backend": "mock", "real_inference": False, "model": "fixed-test-output"}

    def start(self, setting, log_path=None):
        self.setting = dict(setting)
        self.info = {**self.info, "effective_context": setting.get("context_per_request"), "load_s": 0}

    def infer(self, images, prompt, setting, seed):
        # Does not read answers or annotations. This is deliberately not a quality model.
        raw = json.dumps({"keywords": ["모의실행"], "reason": "저장·검증·리포트 경로를 확인하는 고정 응답입니다.",
                          "place_name": None}, ensure_ascii=False)
        return {"raw_output": raw, "input_tokens": None, "output_tokens": None,
                "finish_reason": "mock", "inference_s": 0.0, "vision_tokens_actual": None,
                "real_inference": False}

    def pad_prompt(self, prompt, target_extra):
        return prompt + "\n[모의 용량 입력]\n" + "중립 문장. " * min(target_extra, 50)

    def close(self):
        pass


class TransformersBackend:
    """Colab CUDA에서 QAT 학습 체크포인트를 BF16으로 실행합니다."""
    def __init__(self, cfg):
        self.cfg, self.model, self.pid = cfg, None, None
        self.info = {"backend": "transformers", "real_inference": True}

    def start(self, setting, log_path=None):
        if self.model is not None:
            return
        import os
        import torch
        from transformers import AutoModelForImageTextToText, AutoProcessor

        if not torch.cuda.is_available():
            raise BenchError("unsupported", "A의 실제 추론은 CUDA GPU가 필요합니다. CPU 확인은 --mock을 사용하세요.")
        self.torch, self.pid = torch, os.getpid()
        start = time.perf_counter()
        self.model = AutoModelForImageTextToText.from_pretrained(
            self.cfg["model_id"], revision=self.cfg.get("revision", "main"),
            dtype=torch.bfloat16, device_map={"": "cuda:0"}, attn_implementation="sdpa")
        self.model.eval()
        revision = getattr(self.model.config, "_commit_hash", None) or self.cfg.get("revision", "main")
        self.processor = AutoProcessor.from_pretrained(self.cfg["model_id"], revision=revision, padding_side="left")
        self.info.update({"model": self.cfg["model_id"], "resolved_revision": revision,
                          "model_label": self.cfg.get("model_label"),
                          "model_type": self.model.config.model_type,
                          "checkpoint_training": self.cfg.get("checkpoint_training"),
                          "dtype": "bfloat16", "quantization": "none", "device": torch.cuda.get_device_name(0),
                          "device_memory_bytes": torch.cuda.get_device_properties(0).total_memory,
                          "device_map": {k: str(v) for k, v in self.model.hf_device_map.items()},
                          "load_s": time.perf_counter() - start})

    def infer(self, images, prompt, setting, seed):
        from PIL import Image
        from transformers import set_seed

        torch = self.torch
        set_seed(seed)
        pics = [Image.open(io.BytesIO(b)).convert("RGB") for b in images]
        messages = [{"role": "user", "content": [{"type": "image"} for _ in pics]
                     + [{"type": "text", "text": prompt}]}]
        started = time.perf_counter()
        torch.cuda.reset_peak_memory_stats()
        text = self.processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True,
                                                   enable_thinking=False)
        # 해상도와 별개로, 이미지 한 장에 사용할 시각 토큰의 상한을 전달합니다.
        inputs = self.processor(text=text, images=pics, return_tensors="pt", add_special_tokens=False,
                                max_soft_tokens=setting["vision_token_budget"])
        n_input = inputs["input_ids"].shape[-1]
        image_token = getattr(self.processor, "image_token_id", None)
        visual = int((inputs["input_ids"] == image_token).sum()) if image_token is not None else None
        context = setting["context_per_request"]
        if n_input + setting["max_new_tokens"] > context:
            raise BenchError("input_too_long", f"input {n_input} + output {setting['max_new_tokens']} > {context}")
        # 이미지 텐서는 BF16으로 옮깁니다. 토큰 ID는 정수 자료형을 유지합니다.
        inputs = inputs.to(device=self.model.device, dtype=self.model.dtype)
        temperature = setting["temperature"]
        options = {"do_sample": temperature > 0, "max_new_tokens": setting["max_new_tokens"],
                   "max_time": self.cfg.get("request_timeout_s", 180), "use_cache": True}
        if temperature > 0:
            options.update(temperature=temperature, top_p=0.95, top_k=64)
        torch.cuda.synchronize()
        inference_start = time.perf_counter()
        with torch.inference_mode():
            output = self.model.generate(**inputs, **options)
        torch.cuda.synchronize()
        elapsed = time.perf_counter() - inference_start
        tokens = output[0, n_input:]
        eos = self.model.generation_config.eos_token_id
        eos = eos if isinstance(eos, list) else [eos]
        stopped = bool(len(tokens) and int(tokens[-1]) in eos)
        finish = "stop" if stopped else "length"
        if not stopped and elapsed >= options["max_time"]:
            finish = "timeout"
        return {"raw_output": self.processor.decode(tokens, skip_special_tokens=True),
                "raw_output_with_special_tokens": self.processor.decode(tokens, skip_special_tokens=False),
                "input_tokens": int(n_input), "output_tokens": len(tokens), "finish_reason": finish,
                "vision_tokens_actual": visual, "vision_budget_passed_to_processor": setting["vision_token_budget"],
                "processor_s": inference_start - started, "inference_s": elapsed,
                "torch_peak_allocated_bytes": torch.cuda.max_memory_allocated(),
                "torch_peak_reserved_bytes": torch.cuda.max_memory_reserved(), "real_inference": True}

    def close(self):
        if self.model is not None:
            del self.model
            self.model = None
            self.torch.cuda.empty_cache()


class LlamaBackend:
    """직접 시작한 llama-server만 관리합니다. 다른 앱의 서버는 건드리지 않습니다."""
    def __init__(self, cfg):
        self.cfg, self.process, self.pid, self.log_file = cfg, None, None, None
        self.info = {"backend": "llama-server", "real_inference": True}

    def request(self, route, body=None, timeout=10):
        payload = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
        request = urllib.request.Request(self.url + route, data=payload,
                                         headers={"Content-Type": "application/json"})
        # Local-only: ignore HTTP_PROXY so image data cannot be routed through a proxy.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(request, timeout=timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as e:
            message = e.read().decode(errors="replace")
            raise BenchError(classify_error(message), f"HTTP {e.code}: {message[:2000]}") from e
        except (TimeoutError, socket.timeout) as e:
            raise BenchError("timeout", str(e)) from e
        except urllib.error.URLError as e:
            status = "timeout" if isinstance(e.reason, (TimeoutError, socket.timeout)) else "backend_error"
            raise BenchError(status, str(e)) from e

    def start(self, setting, log_path=None):
        self.close()
        for key in ["server_path", "model_path", "mmproj_path"]:
            if not Path(self.cfg[key]).is_file():
                raise BenchError("unsupported", f"Set an existing {key}: {self.cfg[key]}")
        executable = str(Path(self.cfg["server_path"]).resolve())
        help_result = subprocess.run([executable, "--help"], capture_output=True, text=True, timeout=30)
        help_text = help_result.stdout + help_result.stderr
        required = ["--fit", "--no-context-shift", "--image-max-tokens", "--chat-template-kwargs"]
        if any(flag not in help_text for flag in required):
            raise BenchError("unsupported", "llama-server 빌드가 필요한 옵션을 지원하지 않습니다. README의 공식 문서를 확인하세요.")
        if "model_sha256" not in self.info:
            self.info.update(model_sha256=file_hash(self.cfg["model_path"]),
                             projector_sha256=file_hash(self.cfg["mmproj_path"]),
                             server_sha256=file_hash(executable))
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        self.url = f"http://127.0.0.1:{port}"
        slots = setting["concurrency"]
        command = [executable, "-m", self.cfg["model_path"], "--mmproj", self.cfg["mmproj_path"],
                   "--host", "127.0.0.1", "--port", str(port), "--fit", "off", "--no-context-shift",
                   "-c", str(setting["context_per_request"] * slots), "-np", str(slots),
                   "-ngl", str(self.cfg.get("gpu_layers", 999)), "--slots", "--jinja",
                   "--reasoning", "off", "--reasoning-format", "deepseek",
                   "--image-max-tokens", str(setting["vision_token_budget"]),
                   "-b", str(self.cfg.get("engine_token_batch", 512)),
                   "-ub", str(self.cfg.get("engine_microbatch", 128))]
        self.log_file = Path(log_path).open("w", encoding="utf-8")
        start = time.perf_counter()
        self.process = subprocess.Popen(command, stdout=self.log_file, stderr=subprocess.STDOUT,
                                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        self.pid = self.process.pid
        try:
            deadline = start + self.cfg.get("startup_timeout_s", 180)
            while time.perf_counter() < deadline:
                if self.process.poll() is not None:
                    message = Path(log_path).read_text(encoding="utf-8", errors="replace")[-4000:]
                    raise BenchError(classify_error(message), f"Server exited. {message}")
                try:
                    self.request("/health", timeout=2)
                    break
                except BenchError:
                    time.sleep(0.2)
            else:
                raise BenchError("timeout", "Server startup timed out")
            props, live_slots = self.request("/props"), self.request("/slots")
            contexts = [x.get("n_ctx") for x in live_slots]
            if len(live_slots) != slots or any(n != setting["context_per_request"] for n in contexts):
                raise BenchError("unsupported", f"Actual slots/context differ: slots={len(live_slots)}, n_ctx={contexts}")
            self.info.update(command=command, props=props, effective_context=contexts[0],
                             slots=slots, load_s=time.perf_counter() - start,
                             requested_gpu_layers=self.cfg.get("gpu_layers", 999),
                             quantization_label=self.cfg.get("quantization_label", "record actual GGUF quantization"),
                             model_label=self.cfg.get("model_label"),
                             declared_source_model_id=self.cfg.get("source_model_id"),
                             gpu_offload_verified=False, model_path=self.cfg["model_path"],
                             vision_budget_requested=setting["vision_token_budget"], vision_budget_verified=False)
        except BaseException:
            self.close()
            raise

    def infer(self, images, prompt, setting, seed):
        content = [{"type": "image_url", "image_url": {
            "url": "data:image/png;base64," + base64.b64encode(b).decode()}} for b in images]
        content.append({"type": "text", "text": prompt})
        body = {"model": "local", "messages": [{"role": "user", "content": content}], "stream": False,
                "temperature": setting["temperature"], "seed": seed, "max_tokens": setting["max_new_tokens"],
                "cache_prompt": False, "chat_template_kwargs": {"enable_thinking": False},
                "reasoning_effort": "none"}
        start = time.perf_counter()
        response = self.request("/v1/chat/completions", body, self.cfg.get("request_timeout_s", 180))
        choice = response["choices"][0]
        usage = response.get("usage", {})
        return {"raw_output": choice["message"].get("content") or "", "raw_response": response,
                "input_tokens": usage.get("prompt_tokens"), "output_tokens": usage.get("completion_tokens"),
                "finish_reason": choice.get("finish_reason"), "inference_s": time.perf_counter() - start,
                "vision_tokens_actual": None, "real_inference": True,
                "input_truncated_reported": response.get("truncated")}

    def pad_prompt(self, prompt, target_extra):
        # Estimate token delta with the running model's tokenizer; verify actual usage after inference.
        prefix = prompt + "\n[용량 측정용 중립 텍스트: 장면 분석의 근거로 사용하지 마세요.]\n"
        phrase = "자료의 내용을 추정하지 않는 용량 측정용 문장입니다.\n"
        base = len(self.request("/tokenize", {"content": prompt})["tokens"])
        lo, hi = 0, max(1, target_extra)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            count = len(self.request("/tokenize", {"content": prefix + phrase * mid})["tokens"]) - base
            if count <= target_extra:
                lo = mid
            else:
                hi = mid - 1
        return prefix + phrase * lo if lo else prompt

    def close(self):
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=5)
            self.process, self.pid = None, None
        if self.log_file is not None:
            self.log_file.close()
            self.log_file = None


def make_backend(cfg, mock=False):
    if mock:
        return MockBackend(cfg)
    return {"transformers": TransformersBackend, "llama-server": LlamaBackend}[cfg["kind"]](cfg)
