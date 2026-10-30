// C ABI of the Rust core (src/ffi.rs). Keep the two in step.

#pragma once

#include <cstdint>

extern "C" {

// Opaque: a texture the core has shrunk and keeps (src/texture.rs).
struct ThTexture;

struct ThTextureTile {
    int32_t tile;               // UDIM number, 0 for a plain texture
    ThTexture const* texture;
};

struct ThMeshView {
    float const* points;        // point_count xyz, object space
    uint32_t point_count;
    uint32_t const* triangles;  // triangle_count index triples
    uint32_t triangle_count;
    float const* corner_normals;  // optional, 9 per triangle
    float const* corner_colors;   // optional, 9 per triangle, linear rgb
    float const* corner_uvs;      // optional, 6 per triangle
    float base_color[3];
    ThTextureTile const* tiles;
    uint32_t tile_count;
    double const* transforms;   // transform_count x 16, USD row-vector layout
    uint32_t transform_count;
    uint32_t faceted;           // nonzero: polygonal; drawn flat when no corner_normals
};

struct ThFrame {
    uint32_t width;
    uint32_t height;
    double world_to_clip[16];
    double view_to_world[16];
    uint32_t supersample;
};

// 0 = ok, 1 = bad arguments, 2 = the core panicked.
int32_t th_render(ThFrame const* frame, ThMeshView const* meshes, uint32_t mesh_count,
                  float* color, float* depth);

// pixels: width * height linear RGBA, row 0 at the top. Null on failure.
ThTexture* th_texture_create(float const* pixels, uint32_t width, uint32_t height);
void th_texture_free(ThTexture* texture);

}  // extern "C"
