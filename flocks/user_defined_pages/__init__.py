"""User-space user-defined custom pages under ~/.flocks/plugins/user_defined_pages."""

from flocks.user_defined_pages.store import UserDefinedPagesStore
from flocks.user_defined_pages.watcher import UserDefinedPagesWatcher

__all__ = ["UserDefinedPagesStore", "UserDefinedPagesWatcher"]
