-- Local functions inside the request scope; no global controller registry.
fn scQuote s = (
    local bs = bit.intAsChar 92 as string
    local dq = bit.intAsChar 34 as string
    s = substituteString (s as string) bs (bs+bs)
    s = substituteString s dq (bs+dq)
    s = substituteString s "\n" (bs+"n")
    s = substituteString s "\r" (bs+"r")
    s = substituteString s "\t" (bs+"t")
    dq+s+dq
)
fn scJSON v = (
    case (classof v) of (
        UndefinedClass: "null"
        BooleanClass: (if v then "true" else "false")
        Integer: (v as string)
        Integer64: (formattedPrint v format:"d")
        Float: (
            if (dotNetClass "System.Double").IsNaN v or (dotNetClass "System.Double").IsInfinity v do throw "Non-finite output"
            formattedPrint v format:".9g"
        )
        Point3: (scJSON #(v.x,v.y,v.z))
        Quat: (scJSON #(v.x,v.y,v.z,v.w))
        Array: (
            local out = "["
            for i = 1 to v.count do (if i > 1 do out += ","; out += scJSON v[i])
            out+"]"
        )
        default: (scQuote v)
    )
)
fn scHash text = (
    local sha = (dotNetClass "System.Security.Cryptography.SHA256").Create()
    local bytes = (dotNetClass "System.Text.Encoding").UTF8.GetBytes text
    local result = (dotNetClass "System.BitConverter").ToString (sha.ComputeHash bytes)
    sha.Dispose(); result as string
)
fn scHandle value = (if value == undefined then 0 else ((getHandleByAnim value) as integer64))
fn scNode spec = (
    local node
    if spec[2] != 0 then (
        node = getAnimByHandle spec[2]
        if not isValidNode node do throw "Node handle no longer resolves"
        if spec[1] != "" and node.name != spec[1] do throw "Node handle/name mismatch"
    ) else (
        -- mcpUniqueNode comes from the lookup functions the request prepends.
        node = mcpUniqueNode spec[1] "Node"
    )
    node
)
fn scTrack node steps = (
    local value = node
    for step in steps do (
        if step[1] == "prop" then value = getProperty value (step[2] as name)
        else (
            local key = step[2]
            if classof key == String do key = key as name
            value = value[key]
        )
        if value == undefined do throw "Track no longer resolves"
    )
    if classof value != SubAnim do throw "Destination must resolve to a sub-anim track"
    value
)
fn scIsScript ctrl = (ctrl != undefined and isProperty ctrl #script and isProperty ctrl #throwOnError)
fn scBindings ctrl = (
    local rows = #()
    if scIsScript ctrl do (
        for i = 5 to ctrl.NumVariables() do (
            local kind = ctrl.GetType i
            local value = case kind of (
                #constant: (ctrl.GetConstant i)
                #node: (local n = ctrl.GetNode i; #(scHandle n, if isValidNode n then n.name else undefined))
                #object: (scHandle (ctrl.GetObject i))
                #target: ((ctrl.GetTarget i) as string)
                default: undefined
            )
            -- Include actual reference identity in the token as well as its display path.
            local identity = if kind == #target then scHandle (ctrl.GetTarget i asObject:true) else undefined
            append rows #(ctrl.GetName i, kind as string, value, (ctrl.GetOffset i).ticks, identity)
        )
    )
    rows
)
fn scState node steps sa = (
    local ctrl = sa.controller
    local script = if scIsScript ctrl then ctrl.script else undefined
    local vars = scBindings ctrl
    local state = #(scHandle node, steps, scHandle ctrl, (classof ctrl) as string, script, vars)
    if ctrl != undefined do (
        append state (try ((getBeforeORT ctrl) as string) catch undefined)
        append state (try ((getAfterORT ctrl) as string) catch undefined)
    )
    if scIsScript ctrl do append state ctrl.throwOnError
    -- For an unanimated parameter the static value is its current state.
    if ctrl == undefined do append state sa.value
    scHash (scJSON state)
)
fn scKind sa = (
    local ctrl = sa.controller
    if ctrl != undefined then (
        case (superclassof ctrl) of (
            floatController: #float
            positionController: #position
            rotationController: #rotation
            scaleController: #scale
            point3Controller: #point3
            default: (throw "Unsupported destination controller type")
        )
    ) else (
        case (classof sa.value) of (
            Float: #float
            Integer: #float
            Point3: #point3
            Quat: #rotation
            default: (throw "Unsupported unanimated track type")
        )
    )
)
fn scTypeOK value kind = (
    case kind of (
        #float: (classof value == Float or classof value == Integer)
        #rotation: (classof value == Quat)
        default: (classof value == Point3)
    )
)
fn scRead node steps sa token = (
    local ctrl = sa.controller
    "{\"node\":{\"handle\":" + scJSON (scHandle node) + ",\"name\":" + scQuote node.name +
    "},\"controller_class\":" + scQuote ((classof ctrl) as string) +
    ",\"controller_handle\":" + scJSON (scHandle ctrl) +
    ",\"expected_controller\":" + scQuote token +
    ",\"script\":" + scJSON (if scIsScript ctrl then ctrl.script else undefined) +
    ",\"bindings\":" + scJSON (scBindings ctrl) +
    ",\"throw_on_error\":" + scJSON (if scIsScript ctrl then ctrl.throwOnError else undefined) + "}"
)
fn scRun action target source bindings expected frames = (
    local stage = "target", code = "TRACK_NOT_FOUND", binding = undefined, frame = undefined
    local ownsHold = false, attached = false, restored = undefined
    local node, sa, old, candidate, token, kind, samples = #()
    try (
        node = scNode target[1]
        sa = scTrack node target[2]
        old = sa.controller
        token = scState node target[2] sa
        if expected != undefined and expected != token do (
            code = "STALE_CONTROLLER"; throw "Controller changed since inspection"
        )
        if action == "inspect" and frames == undefined then (
            "{\"ok\":true,\"result\":" + scRead node target[2] sa token + "}"
        ) else (
            code = "USER_BUSY"; stage = "transaction"
            if theHold.Holding() do throw "Finish the active undo operation before evaluating or applying a controller"
            theHold.Begin(); ownsHold = true
            stage = "destination"; code = "OUTPUT_TYPE_MISMATCH"
            kind = scKind sa
            if action == "inspect" do (
                if not scIsScript old do throw "Sampling requires an existing script controller"
                source = old.script
            )
            if scIsScript old then (candidate = copy old) else (
                case kind of (
                    #float: (candidate = float_script())
                    #position: (candidate = position_script())
                    #rotation: (candidate = rotation_script())
                    #scale: (candidate = scale_script())
                    #point3: (candidate = point3_script())
                )
            )
            -- Disable expression errors while replacing bindings on a copied controller.
            candidate.throwOnError = false
            stage = "bindings"; code = "BINDING_NOT_FOUND"
            if bindings != undefined do (
                for i = candidate.NumVariables() to 5 by -1 do candidate.DeleteVariable i
                for spec in bindings do (
                    binding = spec[1]
                    local success = false
                    case spec[2] of (
                        "constant": (
                            local value = spec[3][2]
                            case spec[3][1] of (
                                "point3": value = point3 value[1] value[2] value[3]
                                "quat": value = quat value[1] value[2] value[3] value[4]
                            )
                            success = candidate.AddConstant binding value
                        )
                        "node": (
                            local sourceNode = scNode spec[3]
                            if sourceNode == node do (
                                code = "DEPENDENCY_CYCLE"
                                throw "A node input cannot reference its own driven node; use an independent input node"
                            )
                            success = candidate.AddNode binding sourceNode
                        )
                        default: (
                            local sourceNode = scNode spec[3][1]
                            local track = scTrack sourceNode spec[3][2]
                            if sourceNode == node do (
                                code = "DEPENDENCY_CYCLE"
                                throw "Same-node track inputs require a separate driver node"
                            )
                            if spec[2] == "track" then success = candidate.AddTarget binding track Offset:spec[4]
                            else (
                                if track.controller == undefined do throw "Binding track has no controller"
                                success = candidate.AddObject binding track.controller
                            )
                        )
                    )
                    if not success do throw "Max refused the binding"
                )
            )
            binding = undefined
            stage = "compile"; code = "SCRIPT_COMPILE_ERROR"
            local parameterNames = "T S F NT this"
            local invocation = "__mcpProbe (candidate.GetVarValue 1) (candidate.GetVarValue 2) (candidate.GetVarValue 3) (candidate.GetVarValue 4) candidate"
            for i = 5 to candidate.NumVariables() do (
                parameterNames += " " + candidate.GetName i
                invocation += " (candidate.GetVarValue " + (i as string) + ")"
            )
            -- Compile without executing user code. The function name stays local.
            local probe = execute ("(fn __mcpProbe " + parameterNames + " = (\n" + source + "\n))")
            -- Calling a variable-arity MAXScript function requires a generated local call.
            local evaluate = execute ("(fn __mcpEval __mcpProbe candidate = (" + invocation + "))")
            if frames == undefined do frames = #((currentTime as float) / ticksPerFrame)
            for f in frames do at time f (
                frame = f; stage = "evaluate"; code = "SCRIPT_RUNTIME_ERROR"
                local value = evaluate probe candidate
                code = "OUTPUT_TYPE_MISMATCH"
                if not scTypeOK value kind do throw ("Expected " + (kind as string) + ", got " + ((classof value) as string))
                scJSON value -- reject NaN/Infinity before assignment
            )
            stage = "controller"; code = "SCRIPT_RUNTIME_ERROR"
            candidate.throwOnError = true
            if not candidate.SetExpression source do throw "Max rejected the expression"
            for f in frames do at time f (
                frame = f
                candidate.Update()
                local value = candidate.value
                append samples #(f,value)
            )
            if action == "apply" then (
                stage = "attach"; code = "DEPENDENCY_CYCLE"
                -- The real assignment is authoritative about reference loops.
                candidate.throwOnError = false
                sa.controller = candidate; attached = true
                stage = "verify"; code = "ASSIGNMENT_FAILED"
                if sa.controller != candidate do throw "Assigned controller identity did not match"
                code = "SCRIPT_RUNTIME_ERROR"
                candidate.throwOnError = true
                samples = #()
                for f in frames do at time f (
                    frame = f
                    local verifiedValue = evaluate probe candidate
                    code = "OUTPUT_TYPE_MISMATCH"
                    if not scTypeOK verifiedValue kind do throw "Wrong output type after assignment"
                    scJSON verifiedValue
                    code = "SCRIPT_RUNTIME_ERROR"
                    candidate.Update()
                    append samples #(f,candidate.value)
                )
                -- This does not stop Max's exception box for a later failing frame;
                -- the native sweeper closes that. Inspect with frames to diagnose.
                candidate.throwOnError = false
                local result = scRead node target[2] sa (scState node target[2] sa)
                result = substring result 1 (result.count-1)
                result += ",\"samples\":" + scJSON samples + ",\"applied\":true,\"runtime_error_policy\":\"max_default_no_throw\"}"
                theHold.Accept "MCP script controller"; ownsHold = false
                "{\"ok\":true,\"result\":" + result + "}"
            ) else (
                local result = "{\"expected_controller\":" + scQuote token + ",\"output_type\":" + scQuote kind +
                    ",\"samples\":" + scJSON samples + ",\"applied\":false}"
                theHold.Cancel(); ownsHold = false
                "{\"ok\":true,\"result\":" + result + "}"
            )
        )
    ) catch (
        local message = getCurrentException() as string
        if (stage == "target" or stage == "bindings") and (findString message "Ambiguous object name") != undefined do code = "AMBIGUOUS"
        if candidate != undefined do try (candidate.throwOnError = false) catch ()
        if ownsHold do (
            try (theHold.Cancel(); ownsHold = false) catch (message += "; rollback failed: " + (getCurrentException() as string))
        )
        if sa != undefined do restored = try (sa.controller == old) catch false
        "{\"ok\":false,\"error\":{\"code\":" + scQuote code + ",\"stage\":" + scQuote stage +
            ",\"message\":" + scQuote message + ",\"binding\":" + scJSON binding + ",\"frame\":" + scJSON frame +
            ",\"previous_controller_preserved\":" + scJSON restored + ",\"retryable\":" + (if code == "USER_BUSY" then "true" else "false") + "}}"
    )
)
