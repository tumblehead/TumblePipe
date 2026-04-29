#include "resolver.h"
#include "th_resolver_core.h"

#include <pxr/base/tf/diagnostic.h>
#include <pxr/usd/ar/defineResolver.h>
#include <pxr/usd/ar/filesystemAsset.h>

#include <array>
#include <cstdio>
#include <cstdlib>
#include <cstring>

PXR_NAMESPACE_OPEN_SCOPE

namespace {

constexpr const char* kEntityScheme = "entity:";
constexpr size_t kPathBufferSize = 1024;

// Diagnostic logging — gated on TH_RESOLVER_DEBUG. Writes to both
// %TEMP%/tumble-resolver.log and stderr. The file path is the reliable
// channel since Houdini's launcher routes stderr away from any place
// callers can read; stderr is kept as a convenience for in-shell
// fd-2 capture. Production installs stay silent unless the env var is set.
bool _DebugEnabled() {
    static const bool enabled = []() {
        const char* v = std::getenv("TH_RESOLVER_DEBUG");
        return v && *v && std::strcmp(v, "0") != 0;
    }();
    return enabled;
}

const char* _DebugLogPath() {
    static std::string path = []() -> std::string {
        const char* tmp = std::getenv("TEMP");
        if (!tmp) tmp = std::getenv("TMP");
        if (!tmp) tmp = ".";
        std::string p = tmp;
        p += "/tumble-resolver.log";
        return p;
    }();
    return path.c_str();
}

inline void _DebugLog(const char* fn, const char* arg) {
    if (!_DebugEnabled()) return;
    std::FILE* f = std::fopen(_DebugLogPath(), "a");
    if (f) {
        std::fprintf(f, "[tumbleResolver] %s(\"%s\")\n", fn, arg ? arg : "");
        std::fclose(f);
    }
    std::fprintf(stderr, "[tumbleResolver] %s(\"%s\")\n", fn, arg ? arg : "");
    std::fflush(stderr);
}

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

TumbleResolver::TumbleResolver() {
    _DebugLog("ctor", "");
}
TumbleResolver::~TumbleResolver() = default;

std::string TumbleResolver::_CreateIdentifier(
    const std::string& assetPath,
    const ArResolvedPath& /*anchorAssetPath*/) const
{
    _DebugLog("_CreateIdentifier", assetPath.c_str());
    // entity:// URIs are absolute identifiers; anchor has no bearing.
    return assetPath;
}

std::string TumbleResolver::_CreateIdentifierForNewAsset(
    const std::string& assetPath,
    const ArResolvedPath& anchorAssetPath) const
{
    _DebugLog("_CreateIdentifierForNewAsset", assetPath.c_str());
    return _CreateIdentifier(assetPath, anchorAssetPath);
}

ArResolvedPath TumbleResolver::_Resolve(const std::string& assetPath) const {
    _DebugLog("_Resolve", assetPath.c_str());
    if (!_IsEntityUri(assetPath)) {
        _DebugLog("_Resolve:not-entity-uri", assetPath.c_str());
        return ArResolvedPath();
    }
    std::string resolved = _CallRustResolve(assetPath);
    _DebugLog("_Resolve:result", resolved.c_str());
    return resolved.empty() ? ArResolvedPath() : ArResolvedPath(std::move(resolved));
}

ArResolvedPath TumbleResolver::_ResolveForNewAsset(
    const std::string& assetPath) const
{
    _DebugLog("_ResolveForNewAsset", assetPath.c_str());
    // Read-only resolver: write paths go through the default FS resolver.
    return _Resolve(assetPath);
}

ArTimestamp TumbleResolver::_GetModificationTimestamp(
    const std::string& /*assetPath*/,
    const ArResolvedPath& resolvedPath) const
{
    _DebugLog("_GetModificationTimestamp", resolvedPath.GetPathString().c_str());
    // resolvedPath is the filesystem path we returned from _Resolve; the
    // default filesystem timestamp is exactly what we want. Delegating
    // here keeps the Rust side smaller (no stat wrapper needed) and
    // benefits from any ABI-compatible improvements USD ships.
    return ArFilesystemAsset::GetModificationTimestamp(resolvedPath);
}

std::shared_ptr<ArAsset> TumbleResolver::_OpenAsset(
    const ArResolvedPath& resolvedPath) const
{
    _DebugLog("_OpenAsset", resolvedPath.GetPathString().c_str());
    return ArFilesystemAsset::Open(resolvedPath);
}

std::shared_ptr<ArWritableAsset> TumbleResolver::_OpenAssetForWrite(
    const ArResolvedPath& /*resolvedPath*/,
    WriteMode /*writeMode*/) const
{
    _DebugLog("_OpenAssetForWrite", "");
    // Read-only resolver: entity:// assets cannot be written through us.
    // Callers that need to write resolved filesystem paths should use
    // USD's default filesystem resolver on the already-resolved path.
    return nullptr;
}

// Resolver factory registration.
//
// We deliberately do NOT use AR_DEFINE_RESOLVER here. That macro registers
// via TF_REGISTRY_FUNCTION → ARCH_CONSTRUCTOR, which on Windows places an
// `extern const Arch_ConstructorEntry` in the `.pxrctor` section via
// `__declspec(allocate(".pxrctor"))`. The data is unreferenced from
// anywhere else in the DLL, so MSVC's link.exe (observed on 14.44 / VS 2022
// BuildTools, but not on 14.50 / VS 2026) eliminates it as dead data even
// with /WHOLEARCHIVE on the .obj and /OPT:NOREF /OPT:NOICF on the link —
// the .pxrctor section never makes it into the final PE, so
// Arch_ConstructorInit has nothing to walk and Ar_ResolverFactory never
// gets registered. Symptom: TfType.FindByName('TumbleResolver').isUnknown
// is False (USD picks the type up from plugInfo.json metadata), the URI
// scheme registers, but `Ar.GetResolver().Resolve('entity:/...')` throws
// "Failed to manufacture asset resolver".
//
// A regular C++ static initializer goes through `.CRT$XCU` instead, which
// MSVC always preserves regardless of toolchain version. Calling
// Ar_DefineResolver<>() directly is what the AR_DEFINE_RESOLVER macro
// would have done; we just bypass the section-based registry mechanism
// and rely on the standard CRT init path.
namespace {
struct _ResolverRegistrar {
    _ResolverRegistrar() {
        _DebugLog("static_init_marker", "fired");
        Ar_DefineResolver<TumbleResolver, ArResolver>();
    }
};
const _ResolverRegistrar _resolverRegistrar;
}  // namespace

PXR_NAMESPACE_CLOSE_SCOPE
