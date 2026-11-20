# MAXScript: Animation and Controllers Reference

## Time System

Resolution: 4800 ticks/second. Numbers in time contexts = frame counts.

```maxscript
-- Time literals
100f          -- 100 frames
2.5s          -- 2.5 seconds
4800t         -- 4800 ticks = 1 second
1m15s         -- 1 minute 15 seconds
2:10.0        -- SMPTE 2min 10sec 0frames

-- Globals
sliderTime = 50f              -- set/get time slider
currentTime                   -- current MAXScript time context
animationRange                -- (interval 0f 100f)
animationRange = interval 0f 200f
TicksPerFrame                 -- typically 160 (at 30fps)
frameRate                     -- frames per second
normTime 0.5                  -- mid-point of animation range

-- Time properties
t = 50f
t.frame       -- 50
t.ticks       -- 8000
t.normalized  -- 0.5 (fraction of animationRange)
```

## animate / at time Contexts

```maxscript
-- Create keyframes: combine animate on + at time
b = box()
with animate on (
    at time 0  b.pos = [0,0,0]
    at time 50 b.pos = [100,0,0]
    at time 100 b.pos = [100,100,50]
)

-- at time reads interpolated values without animate
at time 25 b.pos   -- interpolated position at frame 25

-- Loop keyframe generation
animate on
for t = 0 to 100 by 5 do
    at time t $foo.pos = random [-50,-50,0] [50,50,0]
```

## Controller Hierarchy & Track Access

Default node transform: `PRS > Position_XYZ > Bezier_Float (per axis)`

```maxscript
-- Access controllers via .controller or .track (synonyms)
$box01.pos.controller                  -- Position_XYZ
$box01.pos.controller.x_position       -- float sub-property
$box01.rotation.controller             -- Euler_XYZ
$box01.scale.controller                -- Bezier_Scale
$box01.transform.controller            -- PRS (top-level)

-- PRS sub-controllers
prs = $box01.transform.controller
prs.position                           -- position sub-controller
prs.rotation                           -- rotation sub-controller
prs.scale                              -- scale sub-controller

-- Property shortcuts
$box01.pos.isAnimated      -- bool
$box01.pos.keys            -- MAXKeyArray (read-only)
$box01.pos.supportsKeys    -- bool
$box01.height.controller   -- float controller on object param
```

## SubAnim (Indexed Track Access)

```maxscript
-- Node subAnims: [1]=Visibility, [2]=SpaceWarps, [3]=Transform, [4]=Object, [5]=Material
$box01[3]                    -- SubAnim:Transform
$box01[3][1]                 -- SubAnim:Position (within transform)
$box01[3][2]                 -- SubAnim:Rotation
$box01[#transform][#position] -- same via names

-- SubAnim properties
sa = $box01[3][1]
sa.value                     -- current value
sa.controller                -- assigned controller
sa.keys                      -- key array
sa.isAnimated                -- bool
sa.numSubs                   -- child count
sa.name                      -- "Position"

-- Enumerate subAnims
for i = 1 to $box01.numSubs do print (getSubAnimName $box01 i)
getSubAnimNames $box01       -- returns name array

-- Object parameter access
b = box()
getSubAnimNames b[4]         -- #(#length, #width, #height, ...)
b[4][3].value                -- height value
```

## Controller Assignment

```maxscript
-- Create and assign
$foo.pos.controller = bezier_position()
$foo.rotation.controller = tcb_rotation()
$foo.height.controller = linear_float()
$foo.transform.controller = prs()

-- Default controller constructors (respect user defaults)
NewDefaultFloatController()       -- Bezier_Float
NewDefaultPositionController()    -- Position_XYZ
NewDefaultRotationController()    -- Euler_XYZ
NewDefaultScaleController()       -- Bezier_Scale
NewDefaultMatrix3Controller()     -- PRS
```

## Controller Types Quick Reference

| Type | Float | Position | Rotation | Scale | Point3 |
|------|-------|----------|----------|-------|--------|
| Bezier | bezier_float | bezier_position | bezier_rotation | bezier_scale | bezier_point3 |
| Linear | linear_float | linear_position | linear_rotation | linear_scale | - |
| TCB | tcb_float | tcb_position | tcb_rotation | tcb_scale | - |
| Noise | noise_float | noise_position | noise_rotation | noise_scale | noise_point3 |
| XYZ | - | Position_XYZ | Euler_XYZ | ScaleXYZ | Point3_XYZ |
| Script | float_script | position_script | rotation_script | scale_script | point3_script |
| Expression | Float_Expression | Position_Expression | - | Scale_Expression | Point3_Expression |
| List | float_list | position_list | rotation_list | scale_list | point3_list |
| Wire | Float_Wire | Position_Wire | Rotation_Wire | Scale_Wire | Point3_Wire |

## Key Creation & Manipulation

```maxscript
c = $box01.pos.controller
-- Add key (returns MAXKey; value = interpolated at that time)
k = addNewKey c 50f
k = addNewKey c 25f #select    -- also selects it

-- Key count and access
numKeys c                       -- number of keys (-1 if not keyframeable)
k = getKey c 2                  -- get 2nd key (1-based)
getKeyTime c 2                  -- time of 2nd key
getKeyIndex c 50f               -- index of key at frame 50

-- Delete keys
deleteKeys c #allKeys           -- delete all
deleteKeys c #selection         -- delete selected only
deleteKey c 3                   -- delete 3rd key

-- Select/deselect keys
selectKeys c (interval 10 50)   -- select keys in range
deselectKeys c                  -- deselect all
selectKey c 2                   -- select by index
isKeySelected c 2               -- query selection

-- Move keys (MUST call sortKeys after!)
moveKeys c 10f                  -- shift all keys +10 frames
moveKeys c 5f #selection        -- shift selected only
moveKey c 2 10f                 -- shift key 2 by 10 frames
sortKeys c                      -- RE-SORT after moving!

-- Copy keys between same-type controllers
appendKey targetCtrl.keys sourceCtrl.keys[2]
```

## Bezier Key Properties

```maxscript
k = addNewKey $box01.pos.controller.x_position.controller 50f
k.time = 50f
k.value = 100.0
k.inTangentType = #smooth    -- #smooth #linear #step #fast #slow #custom #auto
k.outTangentType = #linear
k.inTangent = 0.5            -- float for float ctrl, point3 for position
k.outTangent = 0.2
k.inTangentLength = 0.333    -- 0-1 fraction toward adjacent key
k.outTangentLength = 0.333
k.freeHandle = true           -- allow horizontal handle movement
k.x_locked = false            -- unlock per-axis handles
k.constantVelocity = false
```

## TCB Key Properties

```maxscript
k = addNewKey (tcb_float()) 0f
k.tension = 25.0       -- default 25
k.continuity = 25.0    -- default 25
k.bias = 25.0          -- default 25
k.easeTo = 0.0
k.easeFrom = 0.0
```

## Linear Key Properties

Linear keys have only: `.time`, `.value`, `.selected`

## Out-of-Range (ORT)

```maxscript
-- Types: #constant #cycle #loop #pingPong #linear #relativeRepeat
setBeforeORT c #constant
setAfterORT c #cycle
getBeforeORT c          -- returns name
getAfterORT c
enableORTs c true       -- enable/disable ORT processing
```

## Time Operations

```maxscript
getTimeRange c                          -- interval of all keys
getTimeRange c #selOnly                 -- selected keys range
getTimeRange c #allKeys #children       -- include sub-controllers
setTimeRange c (interval 0f 100f)
deleteTime c (interval 10 50)           -- remove + slide keys
deleteTime c (interval 10 50) #noSlide  -- just delete keys
reverseTime c (interval 0 100)          -- reverse key order
scaleTime c (interval 0 100) 2.0        -- stretch time
insertTime c 50f 20f                    -- insert gap at frame 50
```

## Noise Controllers

```maxscript
c = noise_position()
$box01.pos.controller = c
c.seed = 12345
c.frequency = 0.5
c.fractal = true
c.roughness = 0.5
c.rampin = 10f
c.rampout = 10f
c.noise_strength = [100, 100, 50]   -- point3 for pos/rot/scale
c.x_positive = false                 -- limit to positive values
-- For noise_float: c.noise_strength is a Float, c.positive is Bool
```

## Expression Controllers

```maxscript
c = Float_Expression()
$box01.height.controller = c
-- IExprCtrl interface
c.SetExpression "A * sin(NT * 360)"
c.AddScalarConstant "A" 50.0
c.AddScalarTarget "radius" $sphere01.baseObject[#radius]
c.AddVectorNode "pos" $helper01
c.Update()     -- must call after SetExpression
c.GetExpression()
c.NumScalars()
c.NumVectors()
c.DeleteVariable "A"
-- Built-in vars: NT (normalized time), T (ticks), S (seconds), F (frames)
```

## Script Controllers

For agent authoring, prefer the `script_controller` tool.

In raw MAXScript, configure a detached controller, add its inputs before setting
the expression, and catch both expression assignment and evaluation errors.
`ThrowOnError=false` does not stop Max's Script Controller Exception box and does
not establish that the expression is valid. Controller inputs must be value-only and bounded.

```maxscript
c = float_script()
c.script = "sin(F * 3) * 25 + 50"
$box01.height.controller = c
-- Built-in vars: T (ticks), S (seconds), F (frames), NT (normalized time)

-- Position script example
$box01.pos.controller = position_script()
$box01.pos.controller.script = "[sin(F*2)*50, cos(F*2)*50, F]"

-- IScriptCtrl interface - variables
c.AddConstant "amp" 50.0
c.AddTarget "rad" $sphere01.baseObject[#radius]
c.AddNode "myNode" $helper01
c.AddObject "ctrl" $box01.height.controller
c.SetExpression "amp * rad"
c.Update()
c.NumVariables()                -- 4 built-in + user vars
c.GetValue "amp"
c.DeleteVariable "amp"
```

## Constraints

All constraints share `Interface:constraints` with: `getNumTargets()`, `getNode <i>`, `getWeight <i>`, `setWeight <i> <f>`, `appendTarget <node> <weight>`, `deleteTarget <i>`.

### Position Constraint
```maxscript
pc = Position_Constraint()
$obj.pos.controller = pc
pc.constraints.appendTarget $target1 50.0
pc.constraints.appendTarget $target2 50.0
pc.relative = true    -- keep original offset
```

### Path Constraint
```maxscript
pc = Path_Constraint()
$obj.pos.controller = pc
pc.path = $circle01                  -- assign path
pc.constraints.appendTarget $line01 50.0  -- add more paths
pc.percent = 50.0       -- % along path (animatable)
pc.follow = true         -- align to tangent
pc.bank = true           -- bank on curves
pc.bankAmount = 0.5
pc.constantVel = true    -- even speed
pc.loop = true
pc.axis = 0              -- 0=X, 1=Y, 2=Z
pc.axisFlip = false
```

### LookAt Constraint
```maxscript
lc = LookAt_Constraint()
$obj.rotation.controller = lc
lc.constraints.appendTarget $target01 100.0
lc.target_axis = 2         -- 0=X, 1=Y, 2=Z
lc.target_axisFlip = false
lc.upnode_world = true
lc.StoUP_axis = 1          -- source up axis
lc.relative = false
```

### Orientation Constraint
```maxscript
oc = Orientation_Constraint()
$obj.rotation.controller = oc
oc.constraints.appendTarget $target01 100.0
oc.relative = false
```

### Link Constraint
```maxscript
lc = Link_Constraint()
$child.transform.controller = lc
lc.addWorld frameNo:0           -- unlinked from frame 0
lc.addTarget $parent1 30        -- linked to parent1 from frame 30
lc.addWorld frameNo:60          -- unlinked from frame 60
lc.addTarget $parent2 90        -- linked to parent2 from frame 90
lc.getNumTargets()
lc.getNode 2                    -- $parent1
lc.getFrameNo 2                 -- 30
lc.setFrameNo 2 35              -- change start frame
lc.deleteTarget 3
```

## List Controllers

```maxscript
-- Assign list controller, then add sub-controllers via .available
$box01.pos.controller = position_list()
lst = $box01.pos.controller
lst.available.controller = noise_position()
lst.available.controller = bezier_position()

-- Manage via Interface:list
lst.count                  -- number of sub-controllers
lst.active                 -- index of active controller
lst.setActive 2
lst.getName 1              -- sub-controller name
lst.setName 1 "My Noise"
lst.delete 2               -- remove sub-controller
lst.weight[1] = 50.0       -- weight array
lst.average = true          -- average weights mode

-- Access sub-controllers
lst[1].object              -- first sub-controller
lst[2].object              -- second sub-controller
```

## Wire Parameters

```maxscript
-- One-way wire: source drives target
paramWire.connect $sphere01.baseObject[#radius] $box01.baseObject[#height] "radius * 2"

-- Two-way wire
paramWire.connect2Way \
    $sphere01.baseObject[#radius] \
    $box01.baseObject[#height] \
    "height / 2" "radius * 2"

-- Disconnect
paramWire.disconnect $box01.height.controller

-- Open editor
paramWire.openEditor()
paramWire.editParam $sphere01.baseObject[#radius]

-- Wire controller properties (read-only, created by connect)
wc = $box01.height.controller   -- Float_Wire
wc.numWires          -- number of wires
wc.isDriver          -- bool
wc.isDriven          -- bool
wc.isTwoWay          -- bool
wc.getExprText 1     -- expression string
wc.setExprText 1 "radius * 3"
```

## Reactor Controllers

```maxscript
reactCtrl = Scale_Reactor()    -- also: Float_Reactor, Position_Reactor, Rotation_Reactor, Point3_Reactor
$ball.scale.controller = reactCtrl
reactCtrl.reactions.reactTo $ball.pos.ZPosition.controller
reactCtrl.reactions.setName 1 "Normal"
reactCtrl.reactions.setVectorState 1 [1,1,1]
reactCtrl.reactions.create name:"Squashed"
reactCtrl.reactions.setVectorState 2 [1.5,1.5,0.4]
reactCtrl.reactions.setValueAsFloat 2 10.0
reactCtrl.reactions.setInfluence 1 6.0
reactCtrl.reactions.setStrength 2 1.5
reactCtrl.reactions.setFalloff 1 1.75
reactCtrl.reactions.getCount()
reactCtrl.reactions.getName 1
```

## Spring Controller

```maxscript
sc = SpringPositionController()
$obj.pos.controller = sc
sc.setMass 1.5
sc.setDrag 0.8
sc.addSpring $target01
sc.setTension 1 0.5
sc.setDampening 1 0.3
sc.getSpringCount()
sc.removeSpringByIndex 1
```

## XYZ Controllers Detail

```maxscript
-- Position_XYZ sub-properties
pxyz = $box01.pos.controller     -- Position_XYZ
pxyz.x_position                  -- float (animatable)
pxyz.y_position
pxyz.z_position

-- Euler_XYZ sub-properties
exyz = $box01.rotation.controller -- Euler_XYZ
exyz.x_rotation                   -- float in radians (animatable)
exyz.y_rotation
exyz.z_rotation
exyz.axisOrder = 1                -- 1=XYZ,2=XZY,3=YZX,4=YXZ,5=ZXY,6=ZYX

-- Get axis sub-controllers
ctrls = getXYZControllers $box01.pos.controller  -- array of 3 float controllers
```

## Common Animation Utilities

```maxscript
-- Iterate all keys of a controller
c = $box01.height.controller
for k in c.keys do format "t:% v:%\n" k.time k.value

-- Copy animation from one object to another (same controller type)
for i = 1 to numKeys srcCtrl do (
    k = getKey srcCtrl i
    newK = addNewKey tgtCtrl k.time
    newK.value = k.value
)

-- Bake animation to keys
c = $obj.pos.controller
newC = bezier_position()
for t = animationRange.start to animationRange.end do (
    at time t (
        k = addNewKey newC t
        k.value = $obj.pos
    )
)
$obj.pos.controller = newC

-- Set controller value functions (3ds Max 2017+)
c = $box01.height.controller
setControllerValue c 100 true #absolute   -- set key
setControllerValue c -10 true #relative   -- add relative key
setControllerValue c 200 false #absolute  -- temp change (no key)
CommitControllerValue c                    -- make temp permanent
RestoreControllerValue c                   -- discard temp change

-- Ease/Multiplier curves
-- <controller>.Ease_Curve    -- float sub-property
-- <controller>.Multiplier_Curve -- float sub-property

-- Controller inspection
isController val           -- true if val is a controller
classOf c                  -- e.g. Bezier_Float
superClassOf c             -- e.g. FloatController
c.value                    -- current value (time-sensitive)
c.keys                     -- MAXKeyArray
displayControlDialog c "My Controller"  -- open key info dialog
```
