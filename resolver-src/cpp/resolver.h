#pragma once

#include <pxr/pxr.h>
#include <pxr/usd/ar/resolver.h>
#include <pxr/usd/ar/resolvedPath.h>

#include <memory>

// Export the class so MSVC's /OPT:REF can't drop the COMDAT section
// containing AR_DEFINE_RESOLVER's anonymous static registration. Matches
// USD's own plugin pattern (PXR_*_API macros). No-op on toolchains with
// default visibility.
#if defined(_MSC_VER)
#  define TH_RESOLVER_API __declspec(dllexport)
#elif defined(__GNUC__) || defined(__clang__)
#  define TH_RESOLVER_API __attribute__((visibility("default")))
#else
#  define TH_RESOLVER_API
#endif

PXR_NAMESPACE_OPEN_SCOPE

/// URI-scheme resolver for the `entity://` scheme used by TumblePipe.
///
/// The class is a thin C++ adapter — every path decision is made by the
/// Rust core (`th_resolver_core`) through a narrow C ABI declared in
/// `th_resolver_core.h`. USD only routes `entity:` asset paths to this
/// resolver; filesystem paths continue to flow through USD's default
/// resolver.
class TH_RESOLVER_API TumbleResolver final : public ArResolver {
public:
    TumbleResolver();
    ~TumbleResolver() override;

protected:
    std::string _CreateIdentifier(
        const std::string& assetPath,
        const ArResolvedPath& anchorAssetPath) const override;

    std::string _CreateIdentifierForNewAsset(
        const std::string& assetPath,
        const ArResolvedPath& anchorAssetPath) const override;

    ArResolvedPath _Resolve(const std::string& assetPath) const override;

    ArResolvedPath _ResolveForNewAsset(const std::string& assetPath) const override;

    ArTimestamp _GetModificationTimestamp(
        const std::string& assetPath,
        const ArResolvedPath& resolvedPath) const override;

    std::shared_ptr<ArAsset> _OpenAsset(
        const ArResolvedPath& resolvedPath) const override;

    std::shared_ptr<ArWritableAsset> _OpenAssetForWrite(
        const ArResolvedPath& resolvedPath,
        WriteMode writeMode) const override;
};

PXR_NAMESPACE_CLOSE_SCOPE
