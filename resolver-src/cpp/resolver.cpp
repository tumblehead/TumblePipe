#include "resolver.h"
#include "th_resolver_core.h"

#include <pxr/base/tf/diagnostic.h>
#include <pxr/usd/ar/defineResolver.h>
#include <pxr/usd/ar/filesystemAsset.h>

#include <array>
#include <cstring>

PXR_NAMESPACE_OPEN_SCOPE

AR_DEFINE_RESOLVER(TumbleResolver, ArResolver);

namespace {

constexpr const char* kEntityScheme = "entity:";
constexpr size_t kPathBufferSize = 1024;

bool _IsEntityUri(const std::string& s) {
    return s.compare(0, std::strlen(kEntityScheme), kEntityScheme) == 0;
}

std::string _LastError() {
    std::array<char, 512> buf{};
    size_t len = 0;
    th_resolver_last_error(buf.data(), buf.size(), &len);
    if (len > buf.size()) {
        len = buf.size();
    }
    return std::string(buf.data(), len);
}

/// Call the Rust resolver. On success returns the resolved filesystem
/// path; on failure logs via TF_WARN and returns an empty string, which
/// USD interprets as "unresolved".
std::string _CallRustResolve(const std::string& uri) {
    std::array<char, kPathBufferSize> buf{};
    size_t len = 0;
    const int32_t rc = th_resolver_resolve(
        uri.data(), uri.size(), buf.data(), buf.size(), &len);

    if (rc == 0 && len <= buf.size()) {
        return std::string(buf.data(), len);
    }

    // Grow-and-retry path: the required size is reported in `len` even
    // when the initial buffer was too small.
    if (rc != 0 && len > buf.size()) {
        std::string big(len, '\0');
        size_t len2 = 0;
        const int32_t rc2 = th_resolver_resolve(
            uri.data(), uri.size(), big.data(), big.size(), &len2);
        if (rc2 == 0) {
            big.resize(len2);
            return big;
        }
    }

    TF_WARN("TumbleResolver: %s (uri=%s, rc=%d)",
            _LastError().c_str(), uri.c_str(), rc);
    return {};
}

}  // namespace

TumbleResolver::TumbleResolver() = default;
TumbleResolver::~TumbleResolver() = default;

std::string TumbleResolver::_CreateIdentifier(
    const std::string& assetPath,
    const ArResolvedPath& /*anchorAssetPath*/) const
{
    // entity:// URIs are absolute identifiers; anchor has no bearing.
    return assetPath;
}

std::string TumbleResolver::_CreateIdentifierForNewAsset(
    const std::string& assetPath,
    const ArResolvedPath& anchorAssetPath) const
{
    return _CreateIdentifier(assetPath, anchorAssetPath);
}

ArResolvedPath TumbleResolver::_Resolve(const std::string& assetPath) const {
    if (!_IsEntityUri(assetPath)) {
        return ArResolvedPath();
    }
    std::string resolved = _CallRustResolve(assetPath);
    return resolved.empty() ? ArResolvedPath() : ArResolvedPath(std::move(resolved));
}

ArResolvedPath TumbleResolver::_ResolveForNewAsset(
    const std::string& assetPath) const
{
    // Read-only resolver: write paths go through the default FS resolver.
    return _Resolve(assetPath);
}

ArTimestamp TumbleResolver::_GetModificationTimestamp(
    const std::string& /*assetPath*/,
    const ArResolvedPath& resolvedPath) const
{
    // resolvedPath is the filesystem path we returned from _Resolve; the
    // default filesystem timestamp is exactly what we want. Delegating
    // here keeps the Rust side smaller (no stat wrapper needed) and
    // benefits from any ABI-compatible improvements USD ships.
    return ArFilesystemAsset::GetModificationTimestamp(resolvedPath);
}

std::shared_ptr<ArAsset> TumbleResolver::_OpenAsset(
    const ArResolvedPath& resolvedPath) const
{
    return ArFilesystemAsset::Open(resolvedPath);
}

std::shared_ptr<ArWritableAsset> TumbleResolver::_OpenAssetForWrite(
    const ArResolvedPath& /*resolvedPath*/,
    WriteMode /*writeMode*/) const
{
    // Read-only resolver: entity:// assets cannot be written through us.
    // Callers that need to write resolved filesystem paths should use
    // USD's default filesystem resolver on the already-resolved path.
    return nullptr;
}

PXR_NAMESPACE_CLOSE_SCOPE
