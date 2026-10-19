#include "resolver.h"
#include "th_resolver_core.h"

#include <pxr/base/tf/diagnostic.h>
#include <pxr/usd/ar/defineResolver.h>
#include <pxr/usd/ar/filesystemAsset.h>

#include <algorithm>
#include <array>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <system_error>

PXR_NAMESPACE_OPEN_SCOPE

namespace {

constexpr size_t kPathBufferSize = 1024;

// Diagnostic logging, gated on TH_RESOLVER_DEBUG. Writes to both
// <temp dir>/tumble-resolver.log and stderr. The file is the reliable
// channel since Houdini's launcher routes stderr away from any place
// callers can read; stderr is kept as a convenience for in-shell fd-2
// capture. Production installs stay silent unless the env var is set.
bool _DebugEnabled() {
    static const bool enabled = []() {
        const char* v = std::getenv("TH_RESOLVER_DEBUG");
        return v && *v && std::strcmp(v, "0") != 0;
    }();
    return enabled;
}

// Empty when the platform reports no temp directory; logging then goes to
// stderr only.
const std::string& _DebugLogPath() {
    static const std::string path = []() -> std::string {
        std::error_code ec;
        const std::filesystem::path dir =
            std::filesystem::temp_directory_path(ec);
        return ec ? std::string() : (dir / "tumble-resolver.log").string();
    }();
    return path;
}

inline void _DebugLog(const char* fn, const char* arg) {
    if (!_DebugEnabled()) return;
    const std::string& logPath = _DebugLogPath();
    if (!logPath.empty()) {
        if (std::FILE* f = std::fopen(logPath.c_str(), "a")) {
            std::fprintf(f, "[tumbleResolver] %s(\"%s\")\n", fn, arg ? arg : "");
            std::fclose(f);
        }
    }
    std::fprintf(stderr, "[tumbleResolver] %s(\"%s\")\n", fn, arg ? arg : "");
    std::fflush(stderr);
}

std::string _LastError() {
    std::array<char, 512> buf{};
    size_t len = 0;
    th_resolver_last_error(buf.data(), buf.size(), &len);
    return std::string(buf.data(), std::min(len, buf.size()));
}

/// Call the Rust resolver. On success returns the resolved filesystem
/// path; on failure logs via TF_WARN and returns an empty string, which
/// USD interprets as "unresolved". Scheme checking is the core's job: a
/// non-`entity:` path comes back as a parse error.
std::string _CallRustResolve(const std::string& uri) {
    std::string out(kPathBufferSize, '\0');
    size_t len = 0;
    int32_t rc = th_resolver_resolve(
        uri.data(), uri.size(), out.data(), out.size(), &len);

    // The core reports the size it needs; retry once at exactly that size.
    if (rc == TH_RESOLVER_ERR_BUFFER_TOO_SMALL) {
        out.assign(len, '\0');
        rc = th_resolver_resolve(
            uri.data(), uri.size(), out.data(), out.size(), &len);
    }

    if (rc == TH_RESOLVER_OK) {
        out.resize(len);
        return out;
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
    std::string resolved = _CallRustResolve(assetPath);
    _DebugLog("_Resolve:result", resolved.c_str());
    return resolved.empty() ? ArResolvedPath() : ArResolvedPath(std::move(resolved));
}

ArResolvedPath TumbleResolver::_ResolveForNewAsset(
    const std::string& assetPath) const
{
    _DebugLog("_ResolveForNewAsset", assetPath.c_str());
    return _Resolve(assetPath);
}

ArTimestamp TumbleResolver::_GetModificationTimestamp(
    const std::string& /*assetPath*/,
    const ArResolvedPath& resolvedPath) const
{
    _DebugLog("_GetModificationTimestamp", resolvedPath.GetPathString().c_str());
    // resolvedPath is the filesystem path _Resolve returned, so the
    // filesystem timestamp is exactly the answer.
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
    // Writers publish to plain filesystem paths, which USD's default
    // resolver handles.
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
// and rely on the standard CRT init path. (The object still has to be
// linked in at all, which is what the /WHOLEARCHIVE in CMakeLists.txt is
// for: nothing references this translation unit by symbol.)
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
