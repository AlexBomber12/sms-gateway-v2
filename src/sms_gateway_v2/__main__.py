from __future__ import annotations

import uvicorn

from sms_gateway_v2.app import create_app
from sms_gateway_v2.config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        create_app(),
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":  # pragma: no cover
    main()
