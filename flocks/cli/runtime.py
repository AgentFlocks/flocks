"""Close stores owned by a foreground CLI invocation before its loop exits."""


async def close_cli_resources() -> None:
    from flocks.channel.inbound.session_binding import close_binding_db
    from flocks.storage.storage import Storage
    from flocks.workflow.store import WorkflowStore

    try:
        await WorkflowStore.close()
    finally:
        try:
            await close_binding_db()
        finally:
            await Storage.shutdown()
