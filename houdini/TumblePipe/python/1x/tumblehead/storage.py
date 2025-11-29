from tumblehead.util.uri import Uri

class StorageConvention:

    def _normalize_input(self, uri: Uri) -> tuple[str, list[str]] | None:
        """Convert Uri to (purpose, segments) tuple."""
        if not isinstance(uri, Uri):
            raise TypeError(f"Expected Uri, got {type(uri).__name__}")
        if uri.is_wild():
            return None  # Cannot resolve wildcard URIs
        return uri.purpose, uri.segments

    def resolve(self, uri: Uri):
        raise NotImplementedError()
