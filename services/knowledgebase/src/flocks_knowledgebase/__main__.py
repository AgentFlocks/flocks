import sys

from pydantic import ValidationError


def main() -> None:
    import uvicorn

    from .settings import Settings

    try:
        settings = Settings()
    except ValidationError as exc:
        fields = ", ".join(".".join(str(part) for part in error["loc"]) for error in exc.errors())
        print(f"Invalid knowledgebase configuration fields: {fields}", file=sys.stderr)
        raise SystemExit(2) from None
    uvicorn.run(
        "flocks_knowledgebase.app:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        workers=1,
        access_log=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
