"""Tests de la decisión de GPU/CPU en la transcripción.

Seams bajo test: ``_pick_device`` (qué dispositivo se pide) y
``_looks_like_gpu_error`` (si un fallo huele a GPU). Son puros: no cargan
Whisper ni tocan la GPU. Lo que se afirma:

- Con GPU presente, la elección del usuario se respeta (Windows con GPU queda
  igual).
- Sin GPU, se pide 'cpu' explícito en vez de intentar CUDA y fallar — que es lo
  que pasa por defecto en Linux/macOS.
- El fallback a CPU reconoce tanto errores de Windows ('dll') como de Linux
  (cargar una '.so' de CUDA).
"""

from aurclips.transcribe import _looks_like_gpu_error, _pick_device


class _Cfg:
    def __init__(self, device="auto", compute="auto"):
        self._d = {"whisper.device": device, "whisper.compute_type": compute}

    def get(self, key, default=None):
        return self._d.get(key, default)


def test_con_gpu_se_respeta_lo_que_pidio_el_usuario():
    device, compute = _pick_device(_Cfg("auto", "float16"), False, cuda_available=True)
    assert device == "auto"
    assert compute == "float16"


def test_sin_gpu_auto_baja_a_cpu():
    device, _ = _pick_device(_Cfg("auto"), False, cuda_available=False)
    assert device == "cpu"


def test_sin_gpu_pedir_cuda_explicito_tambien_baja_a_cpu():
    device, _ = _pick_device(_Cfg("cuda"), False, cuda_available=False)
    assert device == "cpu"


def test_force_cpu_manda_sobre_todo():
    device, compute = _pick_device(_Cfg("cuda", "float16"), True, cuda_available=True)
    assert device == "cpu"
    assert compute == "int8"


def test_cpu_explicito_se_queda_en_cpu():
    device, _ = _pick_device(_Cfg("cpu"), False, cuda_available=True)
    assert device == "cpu"


def test_un_error_de_dll_de_windows_huele_a_gpu():
    assert _looks_like_gpu_error(RuntimeError("cublas64_12.dll not found"))


def test_un_error_de_so_de_linux_huele_a_gpu():
    err = OSError("libcudnn_ops_infer.so.8: cannot open shared object file")
    assert _looks_like_gpu_error(err)


def test_un_error_ajeno_no_se_confunde_con_gpu():
    assert not _looks_like_gpu_error(ValueError("archivo de audio corrupto"))
