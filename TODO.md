# 3dsmax-mcp — Tool TODO

## High Priority — Frequent Token Drains
- [x] `set_material_property` — set a property on an object's material (pairs with `inspect_properties target="material"`)
- [x] `set_material_properties` — batch set multiple material properties in one call (bonus tool)
- [x] `assign_material` — create and/or assign a material to objects in one call
- [ ] `create_light` — create lights (Photometric, Arnold, target/free) with common params
- [ ] `create_camera` — create cameras with FOV, target distance, clipping planes
- [ ] `convert_object` — `convertToPoly` / `convertToMesh` (used constantly before booleans, attaching, exporting)

## Medium Priority — Session Setup
- [ ] `set_render_settings` — resolution, output path, renderer selection, sample counts
- [ ] `get_render_settings` — read current render config

## Needs Input
- Which renderers to target? (Arnold, ART, V-Ray, etc.)
- Typical workflow patterns — character/lookdev, archviz, motion graphics?
- Any other repetitive operations that come up often?
