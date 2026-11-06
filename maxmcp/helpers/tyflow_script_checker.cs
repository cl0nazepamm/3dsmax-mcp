// Compile-checks tyFlow Script operator code without running it.
//
// tyFlow compiles each Script operator with Roslyn into an in-memory assembly
// that holds its whole C# API (tyFlow, Point3, Matrix3, tfObj, ...) and a public
// class TF_OP_script_<operator uid>_<n>_<k> carrying the script's fields and
// helpers (tf, f, eventParticleCount, Print, GetFloat, ...). The checker derives
// a class from the newest such class and compiles the code as its body, so the
// code is type-checked against tyFlow's own API. Nothing is emitted or run.
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp;

public static class McpTyScriptChecker
{
    const string ClassPrefix = "TF_OP_script_";

    // Hiding an inherited member is expected: the code redefines the script methods.
    static readonly HashSet<string> Ignored = new HashSet<string> { "CS0108", "CS0114", "CS0109" };

    static readonly string Usings =
        "using System;\nusing System.Collections.Generic;\nusing System.Linq;\nusing System.Text;\n" +
        "using System.Threading;\nusing System.Threading.Tasks;\n";

    // Newest loaded tyFlow script class, or one compiled for the given operator uid.
    public static Type ScriptClass(string operatorUid)
    {
        Type newest = null, forOperator = null;
        foreach (var asm in AppDomain.CurrentDomain.GetAssemblies())
        {
            if (asm.IsDynamic || !string.IsNullOrEmpty(asm.Location)) continue;
            Type[] types;
            try { types = asm.GetTypes(); } catch { continue; }
            foreach (var t in types)
            {
                if (!t.IsPublic || t.DeclaringType != null || !t.Name.StartsWith(ClassPrefix)) continue;
                newest = t;
                if (!string.IsNullOrEmpty(operatorUid) && t.Name.StartsWith(ClassPrefix + operatorUid + "_")) forOperator = t;
            }
        }
        return string.IsNullOrEmpty(operatorUid) ? newest : forOperator;
    }

    // Names of every script class tyFlow compiled for one operator, oldest first.
    public static string CompiledClasses(string operatorUid)
    {
        var names = new List<string>();
        foreach (var asm in AppDomain.CurrentDomain.GetAssemblies())
        {
            if (asm.IsDynamic || !string.IsNullOrEmpty(asm.Location)) continue;
            Type[] types;
            try { types = asm.GetTypes(); } catch { continue; }
            foreach (var t in types)
                if (t.IsPublic && t.DeclaringType == null && t.Name.StartsWith(ClassPrefix + operatorUid + "_")) names.Add(t.Name);
        }
        return string.Join("|", names);
    }

    static MethodInfo rawMetadata;

    // A reference to an assembly loaded from memory. Its metadata is reached
    // through a pointer, and Max's compile helper refuses unsafe code, so a
    // two-line unsafe helper is compiled here once with Roslyn.
    static MetadataReference InMemoryReference(Assembly asm, List<MetadataReference> framework)
    {
        if (rawMetadata == null)
        {
            const string helper =
                "public static class McpRawMetadata { public static unsafe System.IntPtr Get(System.Reflection.Assembly a, out int length) {" +
                " byte* blob; if (!System.Reflection.Metadata.AssemblyExtensions.TryGetRawMetadata(a, out blob, out length)) return System.IntPtr.Zero;" +
                " return (System.IntPtr)blob; } }";
            var compilation = CSharpCompilation.Create("McpRawMetadata_" + Guid.NewGuid().ToString("N"),
                new[] { CSharpSyntaxTree.ParseText(helper) }, framework,
                new CSharpCompilationOptions(OutputKind.DynamicallyLinkedLibrary, allowUnsafe: true));
            using (var image = new MemoryStream())
            {
                if (!compilation.Emit(image).Success) return null;
                rawMetadata = Assembly.Load(image.ToArray()).GetType("McpRawMetadata").GetMethod("Get");
            }
        }
        var args = new object[] { asm, 0 };
        var blob = (IntPtr)rawMetadata.Invoke(null, args);
        if (blob == IntPtr.Zero) return null;
        return AssemblyMetadata.Create(ModuleMetadata.CreateFromMetadata(blob, (int)args[1])).GetReference();
    }

    static List<MetadataReference> References(Assembly script, string extraAssemblies, List<string> notes)
    {
        var refs = new List<MetadataReference>();
        var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        Action<string> add = path =>
        {
            if (string.IsNullOrEmpty(path) || !File.Exists(path) || !seen.Add(Path.GetFileName(path))) return;
            try { refs.Add(MetadataReference.CreateFromFile(path)); } catch { }
        };
        var tpa = AppContext.GetData("TRUSTED_PLATFORM_ASSEMBLIES") as string;
        if (!string.IsNullOrEmpty(tpa))
            foreach (var path in tpa.Split(Path.PathSeparator)) add(path);
        else
            foreach (var path in Directory.GetFiles(Path.GetDirectoryName(typeof(object).Assembly.Location), "*.dll")) add(path);
        // What tyFlow's own compile references beyond the framework.
        foreach (var name in script.GetReferencedAssemblies())
        {
            var loaded = AppDomain.CurrentDomain.GetAssemblies().FirstOrDefault(a => a.GetName().Name == name.Name);
            if (loaded != null && !loaded.IsDynamic) add(loaded.Location);
        }
        var maxRoot = Path.GetDirectoryName(System.Diagnostics.Process.GetCurrentProcess().MainModule.FileName);
        foreach (var raw in (extraAssemblies ?? "").Split(new[] { ';' }, StringSplitOptions.RemoveEmptyEntries))
        {
            var path = raw.Trim().Replace("$MaxRoot", maxRoot);
            if (File.Exists(path)) add(path); else notes.Add("Additional assembly not found: " + raw.Trim());
        }
        var own = InMemoryReference(script, new List<MetadataReference>(refs));
        if (own == null) notes.Add("tyFlow's API assembly could not be referenced");
        else refs.Add(own);
        return refs;
    }

    // ── Runtime exceptions ──────────────────────────────────────────
    // tyFlow catches exceptions thrown by script callbacks and reports nothing.
    // While armed (only around a tool-driven simulation), every first-chance
    // exception whose stack passes through a script class is recorded.
    sealed class Trapped { public string Key, Type, Message, Class, Method; public int Count; }
    static readonly object trapLock = new object();
    static Dictionary<string, Trapped> trapped;
    static EventHandler<System.Runtime.ExceptionServices.FirstChanceExceptionEventArgs> trapHandler;
    [ThreadStatic] static bool inTrap;

    static Type ScriptOwner(Type t)
    {
        for (var c = t; c != null; c = c.DeclaringType)
            if (c.Name.StartsWith(ClassPrefix)) return c;
        return null;
    }

    static void OnFirstChance(object sender, System.Runtime.ExceptionServices.FirstChanceExceptionEventArgs e)
    {
        if (inTrap) return;
        inTrap = true;
        try
        {
            foreach (var frame in new System.Diagnostics.StackTrace(1, false).GetFrames() ?? new System.Diagnostics.StackFrame[0])
            {
                var method = frame.GetMethod();
                var owner = method == null || method.DeclaringType == null ? null : ScriptOwner(method.DeclaringType);
                if (owner == null) continue;
                var ex = e.Exception;
                var key = owner.Name + "|" + method.Name + "|" + ex.GetType().FullName + "|" + ex.Message;
                lock (trapLock)
                {
                    if (trapped == null) break;
                    Trapped hit;
                    if (!trapped.TryGetValue(key, out hit))
                    {
                        if (trapped.Count >= 20) break;
                        hit = new Trapped { Key = key, Type = ex.GetType().FullName, Message = ex.Message, Class = owner.Name, Method = method.Name };
                        trapped[key] = hit;
                    }
                    hit.Count++;
                }
                break;
            }
        }
        catch { }
        finally { inTrap = false; }
    }

    public static void Arm()
    {
        lock (trapLock) trapped = new Dictionary<string, Trapped>();
        if (trapHandler == null)
        {
            trapHandler = OnFirstChance;
            AppDomain.CurrentDomain.FirstChanceException += trapHandler;
        }
    }

    // Stops recording and returns what was caught as a JSON array.
    public static string Disarm()
    {
        if (trapHandler != null)
        {
            AppDomain.CurrentDomain.FirstChanceException -= trapHandler;
            trapHandler = null;
        }
        List<Trapped> hits;
        lock (trapLock) { hits = trapped == null ? new List<Trapped>() : trapped.Values.ToList(); trapped = null; }
        return "[" + string.Join(",", hits.Select(h =>
            "{\"type\":" + Json(h.Type) + ",\"message\":" + Json(h.Message) + ",\"class\":" + Json(h.Class) +
            ",\"method\":" + Json(h.Method) + ",\"count\":" + h.Count + "}")) + "]";
    }

    // tyFlow's generated class calls these; without them its compile fails
    // (live-tested on 2.05; postSimulationStep and //__END__ are optional).
    static readonly string[][] Required =
    {
        new[] { "simulationStart", "0", "void simulationStart()" },
        new[] { "simulationStep", "0", "void simulationStep()" },
        new[] { "simulationStepThreaded", "3", "void simulationStepThreaded(int startInx, int endInx, int threadInx)" },
    };

    static List<string> MissingCallbacks(SyntaxTree tree)
    {
        var declared = tree.GetRoot().DescendantNodes()
            .OfType<Microsoft.CodeAnalysis.CSharp.Syntax.MethodDeclarationSyntax>()
            .Select(m => m.Identifier.ValueText + "/" + m.ParameterList.Parameters.Count)
            .ToList();
        return Required.Where(r => !declared.Contains(r[0] + "/" + r[1])).Select(r => r[2]).ToList();
    }

    static readonly Dictionary<string, string> Aliases = new Dictionary<string, string>
    {
        { "System.Single", "float" }, { "System.Double", "double" }, { "System.Int32", "int" },
        { "System.Int64", "long" }, { "System.UInt32", "uint" }, { "System.Byte", "byte" },
        { "System.Boolean", "bool" }, { "System.String", "string" }, { "System.Void", "void" },
        { "System.Object", "object" },
    };

    static readonly Dictionary<string, string> Operators = new Dictionary<string, string>
    {
        { "op_Addition", "+" }, { "op_Subtraction", "-" }, { "op_Multiply", "*" }, { "op_Division", "/" },
        { "op_UnaryNegation", "-" }, { "op_Equality", "==" }, { "op_Inequality", "!=" },
        { "op_Implicit", "implicit" }, { "op_Explicit", "explicit" },
    };

    static string TypeName(Type t)
    {
        if (t.IsByRef) return TypeName(t.GetElementType());
        if (t.IsArray) return TypeName(t.GetElementType()) + "[]";
        if (t.IsGenericType)
        {
            var name = t.Name.Substring(0, t.Name.IndexOf('`'));
            return name + "<" + string.Join(", ", t.GetGenericArguments().Select(TypeName)) + ">";
        }
        string alias;
        return t.FullName != null && Aliases.TryGetValue(t.FullName, out alias) ? alias : t.Name;
    }

    static string Parameters(MethodBase m)
    {
        return string.Join(", ", m.GetParameters().Select(p =>
            (p.IsOut ? "out " : p.ParameterType.IsByRef ? "ref " : "") + TypeName(p.ParameterType) + " " + p.Name));
    }

    static bool Internal(string name)
    {
        return name.EndsWith("_") || name.Contains("Internal") || name.StartsWith("<") || name == "__END__" ||
               name.StartsWith("dynamicTFObj") ||
               name == "simulationInit" || name == "SetObjArr" || name == "SetTexArr" || name == "ShiftESCDetected";
    }

    static void Members(StringBuilder sb, Type t, bool scriptClass)
    {
        const BindingFlags flags = BindingFlags.Public | BindingFlags.Instance | BindingFlags.Static | BindingFlags.DeclaredOnly;
        if (t.IsEnum)
        {
            foreach (var name in Enum.GetNames(t)) sb.Append("    ").Append(name).Append(" = ").Append(Convert.ToInt64(Enum.Parse(t, name))).Append('\n');
            return;
        }
        foreach (var field in t.GetFields(flags).Where(x => !Internal(x.Name)).OrderBy(x => x.Name))
            sb.Append("    ").Append(field.IsStatic ? "static " : "").Append(TypeName(field.FieldType)).Append(' ').Append(field.Name).Append(";\n");
        foreach (var prop in t.GetProperties(flags).Where(x => !Internal(x.Name)).OrderBy(x => x.Name))
        {
            var accessor = prop.GetGetMethod() ?? prop.GetSetMethod();
            var index = prop.GetIndexParameters();
            var name = index.Length == 0 ? prop.Name
                : "this[" + string.Join(", ", index.Select(p => TypeName(p.ParameterType) + " " + p.Name)) + "]";
            sb.Append("    ").Append(accessor != null && accessor.IsStatic ? "static " : "")
              .Append(TypeName(prop.PropertyType)).Append(' ').Append(name)
              .Append(prop.CanWrite ? " { get; set; }\n" : " { get; }\n");
        }
        if (!scriptClass)
            foreach (var ctor in t.GetConstructors(BindingFlags.Public | BindingFlags.Instance))
                sb.Append("    ").Append(t.Name).Append('(').Append(Parameters(ctor)).Append(");\n");
        foreach (var method in t.GetMethods(flags).Where(x => !Internal(x.Name) && (!x.IsSpecialName || x.Name.StartsWith("op_"))).OrderBy(x => x.Name))
        {
            if (scriptClass && (Required.Any(r => r[0] == method.Name) || method.Name == "postSimulationStep")) continue;
            string symbol;
            var name = !method.Name.StartsWith("op_") ? method.Name
                : "operator " + (Operators.TryGetValue(method.Name, out symbol) ? symbol : method.Name.Substring(3));
            sb.Append("    ").Append(method.IsStatic && !method.Name.StartsWith("op_") ? "static " : "")
              .Append(TypeName(method.ReturnType)).Append(' ').Append(name).Append('(').Append(Parameters(method)).Append(");\n");
        }
    }

    // The script API of the installed tyFlow, read from its compiled script assembly.
    public static string Api()
    {
        try
        {
            var script = ScriptClass(null);
            if (script == null) return "{\"error\":\"No compiled tyFlow script is loaded in this 3ds Max session yet\"}";
            var sb = new StringBuilder();
            sb.Append("// Callbacks tyFlow calls. Required: simulationStart, simulationStep, simulationStepThreaded.\n");
            foreach (var r in Required) sb.Append("public ").Append(r[2]).Append(" { }\n");
            sb.Append("public void postSimulationStep() { }   // optional\n\n");
            sb.Append("// In scope everywhere in the script (members of the generated script class)\n");
            Members(sb, script, true);
            var skip = new HashSet<string> { "tfcpp", "drawtm_", "drawmarker_", "drawline_" };
            foreach (var t in script.Assembly.GetTypes().Where(x => x.IsPublic && x.DeclaringType == null && !x.Name.StartsWith(ClassPrefix) && !skip.Contains(x.Name)).OrderBy(x => x.Name))
            {
                sb.Append('\n').Append(t.IsEnum ? "enum " : t.IsValueType ? "struct " : "class ").Append(t.Name).Append('\n');
                Members(sb, t, false);
            }
            return "{\"base\":" + Json(script.Name) + ",\"api\":" + Json(sb.ToString()) + "}";
        }
        catch (Exception ex) { return "{\"error\":" + Json("Reading the script API failed: " + ex) + "}"; }
    }

    static string Json(string s)
    {
        var sb = new StringBuilder("\"");
        foreach (var c in s ?? "")
        {
            switch (c)
            {
                case '"': sb.Append("\\\""); break;
                case '\\': sb.Append("\\\\"); break;
                case '\n': sb.Append("\\n"); break;
                case '\r': break;
                case '\t': sb.Append("\\t"); break;
                default:
                    if (c < 32) sb.Append("\\u").Append(((int)c).ToString("x4")); else sb.Append(c);
                    break;
            }
        }
        return sb.Append('"').ToString();
    }

    // code: the operator's script text. members: declarations tyFlow generates
    // for this operator (obj001..., tex001...). Returns JSON:
    // {"ok", "base", "errors", "warnings", "diagnostics":[{severity,id,line,column,message}], "notes"}
    public static string Check(string code, string members, string extraAssemblies)
    {
        try { return CheckCore(code, members, extraAssemblies); }
        catch (Exception ex) { return "{\"error\":" + Json("The C# check failed: " + ex) + "}"; }
    }

    static string CheckCore(string code, string members, string extraAssemblies)
    {
        var notes = new List<string>();
        var baseType = ScriptClass(null);
        if (baseType == null)
            return "{\"error\":\"No compiled tyFlow script is loaded in this 3ds Max session yet\"}";
        // tyFlow ignores everything after the //__END__ marker.
        var lines = (code ?? "").Replace("\r\n", "\n").Split('\n');
        var end = Array.FindIndex(lines, l => l.Trim() == "//__END__");
        var body = string.Join("\n", end >= 0 ? lines.Take(end) : lines);
        var source = Usings + "public class McpTyScriptCheck_ : " + baseType.Name + "\n{\n" + (members ?? "") +
                     "\n#line 1\n" + body + "\n#line default\n}\n";
        var tree = CSharpSyntaxTree.ParseText(source);
        var missing = MissingCallbacks(tree);
        var compilation = CSharpCompilation.Create(
            "McpTyScriptCheck_" + Guid.NewGuid().ToString("N"), new[] { tree },
            References(baseType.Assembly, extraAssemblies, notes),
            new CSharpCompilationOptions(OutputKind.DynamicallyLinkedLibrary, allowUnsafe: true));
        var sb = new StringBuilder();
        int errors = 0, warnings = 0;
        foreach (var signature in missing)
        {
            errors++;
            if (sb.Length > 0) sb.Append(',');
            sb.Append("{\"severity\":\"error\",\"id\":\"TYFLOW001\",\"line\":1,\"column\":1,\"message\":")
              .Append(Json("tyFlow compiles a script only when it declares " + signature)).Append('}');
        }
        foreach (var d in compilation.GetDiagnostics())
        {
            if (d.Severity < DiagnosticSeverity.Warning || Ignored.Contains(d.Id)) continue;
            var span = d.Location.GetMappedLineSpan();
            if (d.Severity == DiagnosticSeverity.Error) errors++; else warnings++;
            if (sb.Length > 0) sb.Append(',');
            sb.Append("{\"severity\":").Append(Json(d.Severity == DiagnosticSeverity.Error ? "error" : "warning"))
              .Append(",\"id\":").Append(Json(d.Id))
              .Append(",\"line\":").Append(span.StartLinePosition.Line + 1)
              .Append(",\"column\":").Append(span.StartLinePosition.Character + 1)
              .Append(",\"message\":").Append(Json(d.GetMessage())).Append('}');
        }
        var notesJson = string.Join(",", notes.Select(Json));
        return "{\"ok\":" + (errors == 0 ? "true" : "false") + ",\"base\":" + Json(baseType.Name) +
               ",\"errors\":" + errors + ",\"warnings\":" + warnings +
               ",\"diagnostics\":[" + sb + "],\"notes\":[" + notesJson + "]}";
    }
}
