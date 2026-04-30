import base64
import io

import pytest
from PIL import Image


def make_png(width: int, height: int) -> str:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode()


@pytest.fixture
def fake_png():
    return make_png
