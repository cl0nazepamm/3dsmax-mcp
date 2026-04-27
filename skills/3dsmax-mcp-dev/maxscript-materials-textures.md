# MAXScript: Materials and Textures Reference

## Material Creation & Assignment

```maxscript
-- Create and assign StandardMaterial
mat = StandardMaterial name:"MyMat" diffuse:red opacity:80.0
$Box001.material = mat

-- Preferred neutral PBR target: OpenPBR.
-- Class spelling differs across Max builds; introspect/discover first.
opbr = undefined
try (opbr = OpenPBRMaterial name:"PBR_Mat") catch ()
if opbr == undefined do try (opbr = OpenPBR_Material name:"PBR_Mat") catch ()
if opbr == undefined do try (opbr = OpenPBR_Mtl name:"PBR_Mat") catch ()
if opbr != undefined do (
    -- Use introspect_class / get_material_slots for exact slot names.
    try (opbr.base_color = color 200 150 100) catch ()
    try (opbr.metalness = 0.0) catch ()
    try (opbr.roughness = 0.3) catch ()
    $Box001.material = opbr
)

-- Physical Material is fallback/legacy when OpenPBR is unavailable.
pmat = PhysicalMaterial name:"PBR_Mat"
pmat.Base_Color = color 200 150 100
pmat.metalness = 0.0
pmat.roughness = 0.3
$Box001.material = pmat

-- Assign material to selection
for obj in selection do obj.material = mat

-- Check material class
classof $Box001.material  --> StandardMaterial
superclassof $Box001.material  --> material
```

## StandardMaterial Properties

```maxscript
m = StandardMaterial()
-- Shader types: 0=Anisotropic 1=Blinn 2=Metal 3=Multi-Layer 4=Oren-Nayar-Blinn 5=Phong 6=Strauss 7=Translucent
m.shaderType = 1          -- Blinn (default)
m.shaderByName = "Blinn"  -- alternative string setter

-- Colors
m.diffuse = color 180 120 80
m.ambient = color 50 50 50
m.specular = color 229 229 229
m.filterColor = color 127 127 127
m.selfIllumColor = color 0 0 0

-- Values
m.opacity = 100.0              -- 0-100
m.specularLevel = 50.0         -- 0-100
m.glossiness = 25.0            -- 0-100
m.selfIllumAmount = 0.0        -- 0-100
m.soften = 0.1
m.ior = 1.5

-- Flags
m.twoSided = false
m.wire = false
m.faceMap = false
m.faceted = false
m.adLock = true          -- ambient/diffuse lock
m.dsLock = false         -- diffuse/specular lock
m.useSelfIllumColor = false
m.opacityType = 0        -- 0=Filter 1=Subtractive 2=Additive
m.showInViewport = true
```

## StandardMaterial Map Slots (Blinn shader)

```maxscript
m = StandardMaterial()
-- Named aliases (preferred over index access)
m.diffuseMap = bitmaptexture filename:@"C:\tex\diffuse.jpg"
m.diffuseMapEnable = true
m.diffuseMapAmount = 100.0

m.bumpMap = noise()
m.bumpMapEnable = true
m.bumpMapAmount = 30.0

m.opacityMap = bitmaptexture filename:@"C:\tex\opacity.png"
m.opacityMapEnable = true

m.reflectionMap = bitmaptexture filename:@"C:\tex\refl.hdr"
m.refractionMap = bitmaptexture filename:@"C:\tex\refr.jpg"
m.displacementMap = bitmaptexture filename:@"C:\tex\disp.exr"
m.specularMap = bitmaptexture filename:@"C:\tex\spec.jpg"
m.specularLevelMap = bitmaptexture filename:@"C:\tex\specLevel.jpg"
m.glossinessMap = bitmaptexture filename:@"C:\tex\gloss.jpg"
m.selfIllumMap = bitmaptexture filename:@"C:\tex\selfillum.jpg"
m.filterMap = bitmaptexture filename:@"C:\tex\filter.jpg"
m.ambientMap = bitmaptexture filename:@"C:\tex\ambient.jpg"

-- Raw array access (Blinn indices): maps[1]=Ambient maps[2]=Diffuse maps[3]=Specular
-- maps[4]=SpecularLevel maps[5]=Glossiness maps[6]=SelfIllum maps[7]=Opacity
-- maps[8]=Filter maps[9]=Bump(index11 for Blinn) maps[10]=Reflection etc.
-- USE NAMED ALIASES INSTEAD -- indices vary per shader type
m.maps[2] = checker()
m.mapEnables[2] = true
m.mapAmounts[2] = 100.0
```

## OpenPBR Preference

Prefer OpenPBR for new neutral PBR materials. Because property/class names can
vary by 3ds Max version, discover before hardcoding:

```maxscript
-- In MCP, prefer:
-- discover_plugin_classes pattern:"*OpenPBR*" superclass:"material"
-- introspect_class class_name:"OpenPBRMaterial"  -- or discovered name
-- get_material_slots name:"ObjectWithOpenPBR" slot_scope:"map"
```

Use PhysicalMaterial only when OpenPBR is unavailable, when converting legacy
Max scenes that already use PhysicalMaterial, or when the user explicitly asks
for Physical.

## Physical Material Properties (fallback/legacy)

```maxscript
p = PhysicalMaterial()
p.material_mode = 0      -- 0=Standard 1=Advanced

-- Base
p.Base_Color = color 180 180 180
p.base_weight = 1.0
p.metalness = 0.0         -- 0=dielectric 1=metal
p.roughness = 0.3
p.roughness_inv = false    -- invert roughness
p.diff_roughness = 0.0

-- Reflections (advanced mode)
p.Reflectivity = 1.0
p.refl_color = white
p.trans_ior = 1.52         -- index of refraction

-- Transparency
p.Transparency = 0.0
p.trans_color = white
p.trans_depth = 0.0
p.thin_walled = false

-- Coating
p.coating = 0.0
p.coat_color = white
p.coat_roughness = 0.0
p.coat_ior = 1.52

-- Emission
p.emission = 0.0
p.emit_color = white
p.emit_luminance = 1500.0
p.emit_kelvin = 6500.0

-- SSS
p.scattering = 0.0
p.sss_color = white
p.sss_depth = 10.0

-- Map slots (use named properties)
p.base_color_map = bitmaptexture filename:@"C:\tex\albedo.jpg"
p.roughness_map = bitmaptexture filename:@"C:\tex\rough.jpg"
p.metalness_map = bitmaptexture filename:@"C:\tex\metal.jpg"
p.bump_map = bitmaptexture filename:@"C:\tex\normal.jpg"
p.bump_map_amt = 1.0
p.displacement_map = bitmaptexture filename:@"C:\tex\disp.exr"
p.cutout_map = bitmaptexture filename:@"C:\tex\opacity.png"
p.emission_map = bitmaptexture filename:@"C:\tex\emit.jpg"
p.coat_map = bitmaptexture filename:@"C:\tex\coat.jpg"
p.transparency_map = bitmaptexture filename:@"C:\tex\trans.jpg"
-- Enable/disable with *_map_on properties
p.base_color_map_on = true
p.roughness_map_on = true
p.bump_map_on = true
```

## BitmapTexture (Loading Bitmaps)

```maxscript
bm = bitmaptexture filename:@"C:\textures\wood.jpg"
bm = bitmaptex filename:@"C:\textures\wood.jpg"  -- short alias

-- Key properties
bm.filename                  -- get/set file path
bm.bitmap                    -- the Bitmap value
bm.filtering = 0             -- 0=Pyramidal 1=SummedArea 2=None
bm.alphasource = 0           -- 0=ImageAlpha 1=RGBIntensity 2=None(Opaque)
bm.monoOutput = 0            -- 0=RGBIntensity 1=Alpha
bm.RGBOutput = 0             -- 0=RGB 1=AlphaAsGray
bm.preMultAlpha = true
bm.coords                    -- StandardUVGen (tiling, offset, etc.)
bm.output                    -- StandardTextureOutput (levels, invert)

-- Cropping/Placement
bm.apply = true
bm.cropPlace = 0             -- 0=Crop 1=Place
bm.clipu = 0.0; bm.clipv = 0.0; bm.clipw = 1.0; bm.cliph = 1.0

-- Animated bitmaps
bm.starttime = 0f
bm.playbackrate = 1.0
bm.endcondition = 0          -- 0=Loop 1=PingPong 2=Hold

-- Methods
bm.reload()                  -- reload file from disk
bm.viewImage()               -- show in VFB

-- Utility functions
usedMaps()                   -- array of all bitmap filenames in scene
usedMaps $Box001             -- bitmaps used by specific object
freeSceneBitmaps()           -- free bitmap caches
```

## UV Coordinates (StandardUVGen)

```maxscript
-- Access via .coords (or .coordinates)
bm = bitmaptexture filename:@"C:\tex\wood.jpg"
bm.coords.mappingType = 0    -- 0=Texture 1=Environment
bm.coords.mapping = 0        -- 0=ExplicitMapChannel 1=VertexColor 2=PlanarObjXYZ 3=PlanarWorldXYZ
bm.coords.mapChannel = 1     -- UV channel number (1-99)
bm.coords.U_Tiling = 2.0
bm.coords.V_Tiling = 2.0
bm.coords.U_Offset = 0.0
bm.coords.V_Offset = 0.0
bm.coords.W_Angle = 45.0     -- rotation in degrees
bm.coords.U_Tile = true      -- enable tiling
bm.coords.V_Tile = true
bm.coords.U_Mirror = false
bm.coords.V_Mirror = false
bm.coords.Blur = 1.0
bm.coords.Blur_Offset = 0.0
bm.coords.realWorldScale = false  -- real-world map size (Max 8+)
```

## Texture Output (StandardTextureOutput)

```maxscript
bm.output.invert = false
bm.output.clamp = false
bm.output.alphaFromRGB = false
bm.output.Output_Amount = 1.0
bm.output.RGB_Offset = 0.0
bm.output.RGB_Level = 1.0
bm.output.Bump_Amount = 1.0   -- only affects bump maps
```

## Common Texture Map Types

```maxscript
-- Checker
c = checker color1:black color2:white soften:0.0
c.map1 = bitmaptexture()    -- sub-map for color1
c.map1Enabled = true

-- Noise
n = noise type:0 size:25.0 color1:black color2:white
-- type: 0=Regular 1=Fractal 2=Turbulence
n.levels = 3.0; n.phase = 0.0
n.thresholdLow = 0.0; n.thresholdHigh = 1.0

-- Gradient
g = gradient gradientType:0 color1:black color2:gray color3:white
-- gradientType: 0=Linear 1=Radial
g.color2Pos = 0.5

-- Gradient Ramp
gr = Gradient_Ramp Gradient_Type:4  -- 0=4Corner 4=Linear 8=Radial 6=Normal

-- Mix (blend two maps/colors by amount or mask)
mx = mix color1:black color2:white mixAmount:50.0
mx.map1 = noise(); mx.map2 = checker()
mx.mask = bitmaptexture filename:@"C:\tex\mask.jpg"
mx.maskEnabled = true

-- Falloff (fresnel, viewing angle, etc.)
fo = falloff type:1 color1:black color2:white
-- type: 0=Towards/Away 1=Perpendicular/Parallel 2=Fresnel 3=Lit/Shadowed 4=DistanceBlend
fo.ior = 1.6  -- for Fresnel type
fo.direction = 0  -- 0=ViewingDir 1=Object 2-4=LocalXYZ 5-7=WorldXYZ

-- Mask
mk = mask()
mk.map = checker()
mk.mask = noise()
mk.maskInverted = false

-- Color Correction (3ds Max 2009+)
cc = ColorCorrection()
cc.Map = bitmaptexture filename:@"C:\tex\diffuse.jpg"

-- Composite Texture Map
ct = compositeTextureMap()
-- ct.blendMode = #(0)  -- array, 0=Normal 5=Multiply 9=Screen 14=Overlay

-- Output (wrapper for output control)
o = output()
o.map1 = noise()
```

## Multi/Sub-Object Material

```maxscript
-- Create with specific count
mm = multimaterial numsubs:5
mm.name = "Wall_MultiMat"

-- Access sub-materials
mm.materialList[1] = StandardMaterial diffuse:red name:"Brick"
mm.materialList[2] = StandardMaterial diffuse:gray name:"Mortar"
mm.materialList[3] = PhysicalMaterial Base_Color:blue name:"Glass"

-- Enable/disable sub-materials
mm.mapEnabled[1] = true

-- Sub-material names (slot names, not material names)
mm.names[1] = "Brick Slot"

-- Material ID list (which face mat ID maps to which sub-material)
mm.materialIDList[1] = 1
mm.materialIDList[2] = 2

-- Change count after creation
mm.numsubs = 8
-- OR
mm.materialList.count = 8

-- Legacy indexed access (still works)
mm[1] = StandardMaterial diffuse:green

-- Iterate sub-materials (use .materialList, not indexed access)
for i = 1 to mm.materialList.count do (
    format "Slot %: %\n" i mm.materialList[i]
)

-- Assign to object (faces need matching Material IDs)
$Box001.material = mm
```

## Blend Material

```maxscript
b = blend()
b.map1 = StandardMaterial diffuse:red     -- material 1
b.map2 = StandardMaterial diffuse:blue    -- material 2
b.mixAmount = 50.0                         -- 0-100 percentage
b.mask = noise()                           -- mask map
b.maskEnabled = true
```

## Material Editor Access

```maxscript
-- Open/Close Material Editor
MatEditor.Open()
MatEditor.Close()
MatEditor.isOpen()
MatEditor.mode = #basic      -- #basic=Compact #advanced=Slate
SME.Open()                   -- open Slate Material Editor directly

-- Access meditMaterials (24 sample slots)
meditMaterials[1]                           -- get slot 1 material
meditMaterials[1] = StandardMaterial()      -- set slot 1
meditMaterials[1].diffuse = red             -- modify in place
meditMaterials.count                        -- always 24
for m in meditMaterials do print m.name     -- iterate all

-- Active slot
activeMeditSlot = 3                         -- set active
idx = activeMeditSlot                       -- get active

-- Functions
getMeditMaterial 3                          -- same as meditMaterials[3]
setMeditMaterial 3 (StandardMaterial())     -- same as meditMaterials[3] = ...

-- medit interface
medit.GetCurMtl()                           -- current selected material
medit.SetActiveMtlSlot 3 true              -- set active + force update
medit.GetActiveMtlSlot()                    -- get active slot index
medit.PutMtlToMtlEditor myMat 3            -- put material in slot
medit.GetTopMtlSlot 3                       -- get material from slot

-- Show texture in viewport
showTextureMap $Box001.material on
showTextureMap $Box001.material $Box001.material.diffuseMap on
-- For Multi/Sub: specify the sub-material
showTextureMap mm[1] tm on
```

## Material Libraries

```maxscript
-- Current material library (global)
currentMaterialLibrary                           -- the active library
currentMaterialLibrary.count                     -- number of materials
currentMaterialLibrary[1]                        -- by index
currentMaterialLibrary["Rough Gold"]             -- by name

-- Scene materials
sceneMaterials                                   -- all materials in scene
for m in sceneMaterials do print m.name
UpdateSceneMaterialLib()                         -- force refresh

-- Load/Save
loadMaterialLibrary @"C:\matlibs\metals.mat"     -- load as current lib
saveMaterialLibrary @"C:\matlibs\output.mat"     -- save current lib
loadDefaultMatLib()                              -- load default lib
getMatLibFileName()                              -- current lib filename

-- Temp libraries (don't affect current lib)
tempLib = loadTempMaterialLibrary @"C:\matlibs\metals.mat"
tempLib[1]                                       -- access materials
saveTempMaterialLibrary tempLib @"C:\matlibs\out.mat"

-- Create custom library
myLib = materialLibrary()
append myLib (StandardMaterial name:"Red" diffuse:red)
append myLib (PhysicalMaterial name:"Chrome" metalness:1.0)
saveTempMaterialLibrary myLib @"C:\matlibs\custom.mat"
deleteItem myLib 1
findItem myLib someMat                           -- returns index or 0

-- Apply from library
$Box001.material = currentMaterialLibrary["Brushed Steel"]

-- File dialogs
fileOpenMatLib()                                 -- open dialog
fileSaveAsMatLib()                               -- save-as dialog
```

## Common Material Methods

```maxscript
-- Shared by all material types
mat.name = "My Material"
mat.effectsChannel = 0
mat.showInViewport = true

assignNewName mat                    -- auto-generate unique name
okMtlForScene mat                    -- true if name is unique in scene

-- Sub-material access (generic, works on any material)
getNumSubMtls mat                    -- number of sub-materials
getSubMtl mat 1                      -- get sub-material by index
setSubMtl mat 1 (StandardMaterial()) -- set sub-material by index
getSubMtlSlotName mat 1              -- get slot name string

-- Sub-texture access (works on materials AND texture maps)
getNumSubTexmaps mat                 -- number of sub-textures
getSubTexmap mat 1                   -- get sub-texture by index
setSubTexmap mat 1 (checker())       -- set sub-texture by index
getSubTexmapSlotName mat 1           -- get slot name string

-- Check if material is used in scene
isMtlUsedInSceneMtl mat             -- true if assigned to any node

-- Render a texture map to bitmap
bmp = renderMap someTexMap size:[512,512] display:true
save bmp
close bmp
```

## Common Patterns

```maxscript
-- PBR setup with Physical Material
fn createPBRMaterial albedoFile roughFile normalFile metalFile = (
    m = PhysicalMaterial()
    if albedoFile != undefined do m.base_color_map = bitmaptexture filename:albedoFile
    if roughFile != undefined do m.roughness_map = bitmaptexture filename:roughFile
    if normalFile != undefined do (
        m.bump_map = bitmaptexture filename:normalFile
        m.bump_map_amt = 1.0
    )
    if metalFile != undefined do m.metalness_map = bitmaptexture filename:metalFile
    m
)

-- Collect all materials from scene objects
allMats = for obj in objects where obj.material != undefined collect obj.material
allMats = makeUniqueArray allMats

-- Find objects with specific material
for obj in objects where obj.material != undefined and obj.material.name == "Glass" do
    format "%\n" obj.name

-- Replace all materials of a type
for obj in objects where classof obj.material == StandardMaterial do
    obj.material = PhysicalMaterial Base_Color:obj.material.diffuse

-- Set material IDs on Editable Poly faces
polyObj = $MyPoly
for f = 1 to polyop.getNumFaces polyObj do
    polyop.setFaceMatID polyObj f (if f <= 10 then 1 else 2)

-- Batch set bitmap gamma
for m in sceneMaterials do (
    for i = 1 to (getNumSubTexmaps m) do (
        t = getSubTexmap m i
        if classof t == BitmapTexture do t.bitmap = openBitmap t.filename gamma:2.2
    )
)

-- Copy material from one object to another (instance)
$Box002.material = $Box001.material
-- Copy material (unique copy)
$Box002.material = copy $Box001.material

-- List all used bitmap filenames
allFiles = usedMaps()
for f in allFiles do format "%\n" f
```
