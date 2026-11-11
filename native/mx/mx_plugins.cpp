// Classes, plugins and their published APIs: class listings and descriptions,
// plugin DLLs, function-publishing interfaces (listed and called), macroscripts,
// action tables, node inspection, OSL shader inspection and guarded plugin
// patches with schema and state tokens.
//
// Listings read the class directory, so deferred plugins stay unloaded until a
// description needs their class descriptor. Interface calls go through
// MAXScript's own function-publishing marshalling: arguments follow MAXScript
// (degrees, 1-based indices and bitarrays, enums by name).
#include "mx_common.h"
#include "mcp_bridge/param_codec.h"

#include <actiontable.h>
#include <iInstanceMgr.h>
#include <imacroscript.h>
#include <maxscript/foundation/3dmath.h>
#include <maxscript/foundation/arrays.h>
#include <maxscript/foundation/colors.h>
#include <maxscript/foundation/functions.h>
#include <maxscript/foundation/mxstime.h>
#include <maxscript/foundation/name.h>
#include <maxscript/foundation/numbers.h>

#include <cstdio>
#include <memory>
#include <set>
#include <tuple>
#include <unordered_set>

namespace mx {
inline namespace MX_ABI_TAG {

using json = nlohmann::json;
using HandlerHelpers::SanitizeScriptName;
using HandlerHelpers::WildcardMatch;

static bool Matches(const std::optional<std::string>& pattern, std::initializer_list<std::string> texts) {
    if (!pattern) return true;
    for (const std::string& text : texts)
        if (WildcardMatch(text, *pattern)) return true;
    return false;
}

static bool SameName(const std::string& a, const std::string& b) { return _stricmp(a.c_str(), b.c_str()) == 0; }

static std::string Hex(ULONG value) {
    char text[16];
    std::snprintf(text, sizeof(text), "0x%08lx", value);
    return text;
}

// ── Class kinds ──────────────────────────────────────────────────
// Short names for the superclasses agents work with; any other superclass
// goes by its MAXScript name (FloatController, ShadowType, ...).

struct KindName { const char* name; SClass_ID sid; };

static const KindName kKinds[] = {
    {"geometry", GEOMOBJECT_CLASS_ID}, {"shape", SHAPE_CLASS_ID}, {"helper", HELPER_CLASS_ID},
    {"light", LIGHT_CLASS_ID}, {"camera", CAMERA_CLASS_ID}, {"spacewarp", WSM_OBJECT_CLASS_ID},
    {"modifier", OSM_CLASS_ID}, {"wsmodifier", WSM_CLASS_ID}, {"material", MATERIAL_CLASS_ID},
    {"texmap", TEXMAP_CLASS_ID}, {"renderer", RENDERER_CLASS_ID}, {"atmospheric", ATMOSPHERIC_CLASS_ID},
    {"effect", RENDER_EFFECT_CLASS_ID}, {"system", SYSTEM_CLASS_ID}, {"utility", UTILITY_CLASS_ID},
};

static std::string SuperclassName(SClass_ID sid) {
    MAXSuperClass* superclass = MAXSuperClass::lookup_superclass(sid);
    if (superclass && superclass->name) return WideToUtf8(superclass->name->to_string());
    return "superclass " + std::to_string(sid);
}

static std::string KindOf(SClass_ID sid) {
    for (const KindName& kind : kKinds)
        if (kind.sid == sid) return kind.name;
    return SuperclassName(sid);
}

// The superclasses a kind or MAXScript superclass name selects; all without one.
static std::vector<SClass_ID> KindSids(const std::optional<std::string>& kind) {
    std::vector<SClass_ID> sids;
    auto& directory = ClassDirectory::GetInstance();
    for (int i = 0; i < directory.Count(); ++i) {
        const SClass_ID sid = directory[i].SuperID();
        if (!kind || SameName(KindOf(sid), *kind) || SameName(SuperclassName(sid), *kind)) sids.push_back(sid);
    }
    if (sids.empty()) {
        std::vector<std::string> names;
        for (const KindName& k : kKinds) names.push_back(k.name);
        throw BadParam("unknown kind '" + *kind + "'." + Suggest(*kind, names) + " Kinds: " + Join(names, 32) +
                       ", or a MAXScript superclass name such as FloatController");
    }
    return sids;
}

// ── Classes ──────────────────────────────────────────────────────

struct ClassInfo {
    SClass_ID sid = 0;
    ULONG a = 0, b = 0;   // Class_ID
};

static ClassInfo MakeClassInfo(SClass_ID sid, const Class_ID& id) { return ClassInfo{sid, id.PartA(), id.PartB()}; }

static ClassEntry& Entry(const ClassInfo& c) {
    RequireMain();
    ClassEntry* entry = ClassDirectory::GetInstance().FindClassEntry(c.sid, Class_ID(c.a, c.b));
    if (!entry) throw NotFound("class (" + Hex(c.a) + ", " + Hex(c.b) + ") is no longer registered");
    return *entry;
}

// Loads a deferred plugin on first use.
static ClassDesc* Descriptor(const ClassInfo& c) {
    ClassDesc* cd = Entry(c).FullCD();
    if (!cd) throw MaxError("3ds Max could not load the descriptor of class (" + Hex(c.a) + ", " + Hex(c.b) + ")");
    return cd;
}

// MAXScript's name for the class, else its non-localized name. Needs a
// MAXScript context.
static std::string ScriptName(SClass_ID sid, ClassEntry& entry) {
    Class_ID id = entry.ClassID();
    MAXClass* cls = MAXClass::lookup_class(&id, sid, false);
    const MCHAR* name = cls && cls->name ? cls->name->to_string() : entry.NonLocalizedClassName().data();
    return SanitizeScriptName(WideToUtf8(name));
}

static std::string DllFile(int index) {
    DllDir& dir = DllDir::GetInstance();
    return index >= 0 && index < dir.Count() ? WideToUtf8(dir[index].GetFileName().data()) : std::string();
}

template <typename F>
static void ForEachEntry(SClass_ID sid, F&& visit) {
    SubClassList* list = ClassDirectory::GetInstance().GetClassList(sid);
    if (!list) return;
    for (int i = list->GetFirst(ACC_ALL); i >= 0; i = list->GetNext(ACC_ALL)) visit((*list)[i]);
}

// Classes of the superclasses that `keep(entry, name)` accepts, by kind and name.
template <typename Keep>
static std::vector<ClassInfo> CollectClasses(const std::vector<SClass_ID>& sids, Keep&& keep) {
    ScopedMaxScriptEvaluationContext context;
    struct Row { std::string kind, name; ClassInfo info; };
    std::vector<Row> rows;
    for (SClass_ID sid : sids) {
        const std::string kind = KindOf(sid);
        ForEachEntry(sid, [&](ClassEntry& entry) {
            const std::string name = ScriptName(sid, entry);
            if (keep(entry, name)) rows.push_back({kind, Lower(name), MakeClassInfo(sid, entry.ClassID())});
        });
    }
    std::sort(rows.begin(), rows.end(), [](const Row& x, const Row& y) {
        return std::tie(x.kind, x.name) < std::tie(y.kind, y.name);
    });
    std::vector<ClassInfo> out;
    for (const Row& row : rows) out.push_back(row.info);
    return out;
}

static std::vector<ClassInfo> Classes(const std::optional<std::string>& kind, const std::optional<std::string>& pattern,
                                      const std::optional<std::string>& plugin) {
    RequireMain();
    ScopedMaxScriptEvaluationContext context;
    return CollectClasses(KindSids(kind), [&](ClassEntry& entry, const std::string& name) {
        return Matches(pattern, {name, WideToUtf8(entry.ClassName().data())}) && Matches(plugin, {DllFile(entry.DllNumber())});
    });
}

static ClassInfo FindClassInfo(const std::string& name, const std::optional<std::string>& kind) {
    RequireMain();
    ScopedMaxScriptEvaluationContext context;
    const std::wstring wanted = Utf8ToWide(name);
    std::vector<std::string> names;
    std::vector<std::pair<ClassInfo, bool>> found;   // class, public
    for (SClass_ID sid : KindSids(kind)) {
        ForEachEntry(sid, [&](ClassEntry& entry) {
            const std::string script = ScriptName(sid, entry);
            names.push_back(script);
            if (SameName(script, name) || HandlerHelpers::MatchesClassName(wanted, entry.ClassName().data()) ||
                HandlerHelpers::MatchesClassName(wanted, entry.NonLocalizedClassName().data()))
                found.push_back({MakeClassInfo(sid, entry.ClassID()), entry.IsPublic() != 0});
        });
    }
    if (found.empty())
        throw NotFound("no class '" + name + "'" + (kind ? " of kind " + *kind : std::string()) + "." + Suggest(name, names));
    std::vector<std::string> kinds;
    for (const auto& match : found)
        if (std::find(kinds.begin(), kinds.end(), KindOf(match.first.sid)) == kinds.end()) kinds.push_back(KindOf(match.first.sid));
    if (kinds.size() > 1)
        throw Ambiguous("'" + name + "' names classes of several kinds (" + Join(kinds) + "); pass kind='" + kinds[0] + "'");
    // Public classes win over private ones of the same name.
    for (const auto& match : found)
        if (match.second) return match.first;
    return found.front().first;
}

static bool Creatable(const ClassInfo& c) {
    if (!Entry(c).IsPublic()) return false;
    ScopedMaxScriptEvaluationContext context;
    Class_ID id(c.a, c.b);
    MAXClass* cls = MAXClass::lookup_class(&id, c.sid, false);
    return !cls || !(cls->md_flags & md_no_create);
}

static std::string CreateHint(SClass_ID sid, const std::string& name) {
    switch (sid) {
    case GEOMOBJECT_CLASS_ID: case SHAPE_CLASS_ID: case HELPER_CLASS_ID: case LIGHT_CLASS_ID: case CAMERA_CLASS_ID:
        return "create('" + name + "', ...)";
    case OSM_CLASS_ID: return "node.add_modifier('" + name + "', ...)";
    case MATERIAL_CLASS_ID: return "material('" + name + "', ...)";
    default: return {};
    }
}

// ── Class descriptions ───────────────────────────────────────────
// The parameters come from the shared parameter codec (param_codec.h), the
// same model the bridge's parameters tool uses: defaults, ranges, named
// choices (published, provider references, rollout radio buttons and
// dropdowns), labels, plus the subanims and MAXScript properties a temporary
// instance shows.

static std::vector<std::string> DescribeClass(const ClassInfo& c) {
    ClassEntry& entry = Entry(c);
    ScopedMaxScriptEvaluationContext context;
    const std::string name = ScriptName(c.sid, entry);
    std::string header = name + ": " + KindOf(c.sid) + " class";
    if (const std::string category = WideToUtf8(entry.Category().data()); !category.empty())
        header += ", category '" + category + "'";
    if (const std::string dll = DllFile(entry.DllNumber()); !dll.empty()) header += ", from " + dll;
    const bool creatable = Creatable(c);
    const std::string hint = creatable ? CreateHint(c.sid, name) : std::string();
    header += hint.empty() ? (creatable ? ", creatable" : ", not creatable") : "; " + hint;
    std::vector<std::string> lines{header};

    ClassDesc* cd = Descriptor(c);
    json described;
    try {
        described = ParamCodec::DescribeClass(cd, Now());
    } catch (const ParamCodec::Error& error) {
        throw BadParam(error.text);
    }
    for (const json& param : described["params"]) lines.push_back(ParamCodec::Line(param));
    if (lines.size() == 1) lines.push_back("no parameters");

    std::vector<std::string> interfaces;
    for (int i = 0; i < cd->NumInterfaces(); ++i) {
        FPInterface* fpi = cd->GetInterfaceAt(i);
        if (FPInterfaceDesc* d = fpi ? fpi->GetDesc() : nullptr)
            interfaces.push_back(WideToUtf8(d->internal_name.data()) + (d->flags & FP_MIXIN ? " (mixin)" : ""));
    }
    if (!interfaces.empty()) lines.push_back("interfaces: " + Join(interfaces, 64) + "; see .interfaces");
    return lines;
}

// ── Scene use ────────────────────────────────────────────────────

// Scene nodes whose object, modifiers, material or controllers reference an
// instance of a class `uses` accepts. The walk stops at other nodes, so a
// scatter does not count its source objects' classes.
template <typename Uses>
static std::vector<Node> NodesUsing(Uses&& uses) {
    RequireMain();
    std::vector<Node> out;
    for (INode* node : AllNodes()) {
        std::unordered_set<ReferenceMaker*> seen{node};
        std::vector<ReferenceMaker*> pending{node};
        bool found = false;
        while (!pending.empty() && !found) {
            ReferenceMaker* maker = pending.back();
            pending.pop_back();
            for (int i = 0; i < maker->NumRefs() && !found; ++i) {
                ReferenceTarget* target = maker->GetReference(i);
                if (!target || target->SuperClassID() == BASENODE_CLASS_ID || !seen.insert(target).second) continue;
                found = uses(target->SuperClassID(), target->ClassID());
                pending.push_back(target);
            }
        }
        if (found) out.push_back(Wrap(node));
    }
    return out;
}

// ── Plugins ──────────────────────────────────────────────────────

struct PluginRef { int index = -1; };   // index in the plugin DLL directory

static const DllDesc& Dll(const PluginRef& p) {
    RequireMain();
    DllDir& dir = DllDir::GetInstance();
    if (p.index < 0 || p.index >= dir.Count()) throw NotFound("plugin #" + std::to_string(p.index) + " is not loaded");
    return dir[p.index];
}

static std::vector<ClassInfo> PluginClasses(const PluginRef& p) {
    Dll(p);
    return CollectClasses(KindSids(std::nullopt), [&](ClassEntry& entry, const std::string&) {
        return entry.DllNumber() == p.index;
    });
}

static std::vector<PluginRef> Plugins(const std::optional<std::string>& pattern) {
    RequireMain();
    DllDir& dir = DllDir::GetInstance();
    std::vector<PluginRef> out;
    for (int d = 0; d < dir.Count(); ++d)
        if (Matches(pattern, {WideToUtf8(dir[d].GetFileName().data()), WideToUtf8(dir[d].Description())}))
            out.push_back(PluginRef{d});
    return out;
}

// ── MAXScript values ─────────────────────────────────────────────

static Animatable* AnimOf(nb::handle value) {
    if (nb::isinstance<Node>(value)) return Resolve(nb::cast<Node>(value));
    if (nb::isinstance<Params>(value)) return ParamOwner(nb::cast<Params>(value));
    if (nb::isinstance<MaterialRef>(value)) {
        RequireMain();
        Animatable* mtl = Animatable::GetAnimByHandle(nb::cast<MaterialRef>(value).mtl);
        if (!mtl || mtl->SuperClassID() != MATERIAL_CLASS_ID) throw NotFound("this material was deleted");
        return mtl;
    }
    return nullptr;
}

// A scene object as its _mx object: Node, Material, else its Params.
static nb::object WrapAnim(Animatable* anim) {
    if (!anim) return nb::none();
    if (anim->SuperClassID() == BASENODE_CLASS_ID) {
        INode* node = static_cast<INode*>(anim);
        return InScene(node) ? nb::cast(Wrap(node)) : nb::none();
    }
    const AnimHandle handle = Animatable::GetHandleByAnim(anim);
    if (anim->SuperClassID() == MATERIAL_CLASS_ID) return nb::cast(MaterialRef{handle});
    return nb::cast(Params{0, handle});
}

// Runs `body(_tls)` in a MAXScript frame; a MAXScript error becomes MaxError.
template <typename F>
static nb::object InMaxScript(const std::string& what, F&& body) {
    RequireMain();
    ScopedMaxScriptEvaluationContext context;
    MAXScript_TLS* _tls = context.Get_TLS();
    MxsTime at(Now());  // calls run at the innermost at(), like other edits
    std::string error;
    nb::object result;
    try {
        ScopedSaveCurrentFrames frames(_tls);
        result = body(_tls);
    } catch (MAXScriptException& e) {
        error = MxsErrorText(e);
        ProcessMAXScriptException(e, _M("_mx"), false, false, true, true);
    }
    if (!error.empty()) throw MaxError(what + ": " + error);
    return result;
}

// Clears its pointer when Max deletes the interface a call returned.
struct InterfaceWatch : InterfaceNotifyCallback {
    FPInterface* fpi = nullptr;
    FPMixinInterface* registered = nullptr;
    void InterfaceDeleted(BaseInterface*) override { fpi = nullptr; registered = nullptr; }
};

// A core interface (by ID), one a class publishes, a mixin on an object (by
// anim handle) or one a call returned (watched).
struct InterfaceRef {
    ULONG a = 0, b = 0;                     // Interface_ID
    SClass_ID sid = 0;                      // the publishing class, for class interfaces
    ULONG ca = 0, cb = 0;
    AnimHandle owner = 0;                   // the object, for mixins
    std::shared_ptr<InterfaceWatch> watch;  // an interface a call returned
};

static Value* InterfaceValue(FPInterface* fpi);
static FPInterface* ResolveInterface(const InterfaceRef& r);
static nb::object WrapInterface(FPInterface* fpi, Value* shown);

static int PlainType(int type) { return type & ~(TYPE_TAB | TYPE_BY_REF | TYPE_BY_VAL | TYPE_BY_PTR); }

static bool FloatLike(int base) {
    return base == TYPE_FLOAT || base == TYPE_ANGLE || base == TYPE_PCNT_FRAC || base == TYPE_WORLD ||
           base == TYPE_COLOR_CHANNEL || base == TYPE_DOUBLE;
}

static void ToValue(Value*& slot, nb::handle v, int type, const std::string& what);

// Lists and tuples: the point, color, matrix or bitarray `base` asks for, else
// an array; an untyped tuple of 2-4 numbers is a point.
static void ToCompound(Value*& slot, nb::handle v, int base, bool tab, const std::string& what) {
    const size_t n = nb::len(v);
    if (!tab) {
        switch (base) {
        case TYPE_POINT3: slot = new Point3Value(ToPoint3(v, what)); return;
        case TYPE_POINT2: { const auto xs = Floats(v, 2, 2, what); slot = new Point2Value(xs[0], xs[1]); return; }
        case TYPE_POINT4: { const auto xs = Floats(v, 4, 4, what); slot = new Point4Value(xs[0], xs[1], xs[2], xs[3]); return; }
        case TYPE_RGBA: case TYPE_COLOR: case TYPE_FRGBA: {
            const auto xs = Floats(v, 3, 4, what);
            slot = new ColorValue(AColor(xs[0], xs[1], xs[2], xs.size() > 3 ? xs[3] : 1.0f));
            return;
        }
        case TYPE_MATRIX3: {
            if (n != 4) throw BadParam(what + " expects 4 rows of (x, y, z)");
            Point3 rows[4];
            for (size_t i = 0; i < 4; ++i) rows[i] = ToPoint3(nb::object(v[i]), what + " row " + std::to_string(i));
            slot = new Matrix3Value(rows[0], rows[1], rows[2], rows[3]);
            return;
        }
        case TYPE_BITARRAY: {
            const std::vector<int> indices = nb::cast<std::vector<int>>(v);
            int size = 0;
            for (int index : indices) {
                if (index < 1) throw BadParam(what + " takes 1-based indices, got " + std::to_string(index));
                size = std::max(size, index);
            }
            auto* bits = new BitArrayValue(size);
            slot = bits;
            for (int index : indices) bits->bits.Set(index - 1);
            return;
        }
        default:
            if (nb::isinstance<nb::tuple>(v) && n >= 2 && n <= 4) {
                const auto xs = Floats(v, 2, 4, what);
                if (n == 2) slot = new Point2Value(xs[0], xs[1]);
                else if (n == 3) slot = new Point3Value(Point3(xs[0], xs[1], xs[2]));
                else slot = new Point4Value(xs[0], xs[1], xs[2], xs[3]);
                return;
            }
        }
    }
    // The array is protected in its slot before its elements are allocated.
    auto* array = new Array((int)n);
    slot = array;
    for (size_t i = 0; i < n; ++i) {
        array->append(&undefined);
        ToValue(array->data[array->size - 1], nb::object(v[i]), tab ? base : TYPE_VALUE, what + "[" + std::to_string(i) + "]");
    }
}

// A Python value as the MAXScript value of a published parameter of `type`
// (TYPE_VALUE: untyped), stored straight into the GC-protected `slot`.
static void ToValue(Value*& slot, nb::handle v, int type, const std::string& what) {
    const int base = PlainType(type);
    try {
        if (v.is_none()) { slot = &undefined; return; }
        if (nb::isinstance<nb::bool_>(v)) { slot = nb::cast<bool>(v) ? &true_value : &false_value; return; }
        if (nb::isinstance<nb::int_>(v)) {
            const long long x = nb::cast<long long>(v);
            if (FloatLike(base)) slot = base == TYPE_DOUBLE ? Double::intern((double)x) : Float::intern((float)x);
            else if (base == TYPE_INT64 || x < INT_MIN || x > INT_MAX) slot = Integer64::intern(x);
            else slot = Integer::intern((int)x);
            return;
        }
        if (nb::isinstance<nb::float_>(v)) {
            const double x = nb::cast<double>(v);
            slot = base == TYPE_DOUBLE ? Double::intern(x) : Float::intern((float)x);
            return;
        }
        if (nb::isinstance<nb::str>(v)) {
            const std::string text = nb::cast<std::string>(v);
            const bool hash = text.size() > 1 && text[0] == '#';
            if (hash || base == TYPE_ENUM || base == TYPE_NAME) slot = Name::intern(Utf8ToWide(hash ? text.substr(1) : text).c_str());
            else slot = new String(Utf8ToWide(text).c_str());
            return;
        }
        if (nb::isinstance<Node>(v)) { slot = MAXNode::intern(Resolve(nb::cast<Node>(v))); return; }
        if (nb::isinstance<InterfaceRef>(v)) { slot = InterfaceValue(ResolveInterface(nb::cast<InterfaceRef>(v))); return; }
        if (Animatable* anim = AnimOf(v)) {
            const bool target = anim->IsRefMaker() && static_cast<ReferenceMaker*>(anim)->IsRefTarget();
            auto* ref = target ? static_cast<ReferenceTarget*>(anim) : nullptr;
            if (!ref) throw BadParam(what + ": this object cannot be passed to MAXScript");
            slot = MAXClass::make_wrapper_for(ref);
            return;
        }
        if (IsSequence(v)) return ToCompound(slot, v, base, (type & TYPE_TAB) != 0, what);
    } catch (const nb::cast_error&) {
        throw BadParam(what + ": cannot convert " + ReprOf(v) + " for 3ds Max");
    }
    throw BadParam(what + ": cannot pass " + WithArticle(TypeNameOf(v)) + " to 3ds Max");
}

static nb::object FromValue(Value* v);

static nb::object FromArray(Array* array) {
    nb::list items;
    for (int i = 0; i < array->size; ++i) items.append(FromValue(array->data[i]));
    return items;
}

static nb::object Shown(Value* v) { return nb::str(MxsShown(v).c_str()); }

// A MAXScript value as Python: numbers, strings, tuples, lists, Node,
// Material, Interface, Params for other scene objects, else its printed form.
static nb::object FromValue(Value* v) {
    if (!v || v == &undefined || v == &unsupplied || v == &ok) return nb::none();
    if (v == &true_value) return nb::bool_(true);
    if (v == &false_value) return nb::bool_(false);
    if (is_integer(v)) return nb::int_(v->to_int());
    if (is_integer64(v) || is_integerptr(v)) return nb::int_((long long)v->to_int64());
    if (is_float(v) || is_double(v)) return nb::float_(v->to_double());
    if (is_string(v) || is_name(v)) return nb::str(WideToUtf8(v->to_string()).c_str());
    if (is_point3(v)) return FromPoint3(v->to_point3());
    if (is_point2(v)) { const Point2 p = v->to_point2(); return nb::make_tuple(p.x, p.y); }
    if (is_point4(v)) { const Point4 p = v->to_point4(); return nb::make_tuple(p.x, p.y, p.z, p.w); }
    if (is_color(v)) {
        const AColor c = v->to_acolor();
        return c.a == 1.0f ? nb::make_tuple(c.r, c.g, c.b) : nb::make_tuple(c.r, c.g, c.b, c.a);
    }
    if (is_matrix3(v)) {
        const Matrix3& m = v->to_matrix3();
        return nb::make_tuple(FromPoint3(m.GetRow(0)), FromPoint3(m.GetRow(1)), FromPoint3(m.GetRow(2)), FromPoint3(m.GetRow(3)));
    }
    if (is_quat(v)) { const Quat q = v->to_quat(); return nb::make_tuple(q.x, q.y, q.z, q.w); }
    if (is_bitarray(v)) {
        nb::list indices;
        BitArray& bits = v->to_bitarray();
        for (int i = 0; i < bits.GetSize(); ++i)
            if (bits[i]) indices.append(i + 1);
        return indices;
    }
    if (is_time(v)) return nb::float_(TicksToFrame(v->to_timevalue()));
    if (is_interval(v)) {
        const Interval range = static_cast<MSInterval*>(v)->interval;
        return nb::make_tuple(TicksToFrame(range.Start()), TicksToFrame(range.End()));
    }
    if (is_array(v)) return FromArray(static_cast<Array*>(v));
    if (is_node(v)) {
        INode* node = v->to_node();
        return InScene(node) ? nb::cast(Wrap(node)) : nb::none();
    }
    if (is_fpstaticinterface(v)) return WrapInterface(static_cast<FPInterfaceValue*>(v)->fpi, v);
    if (is_fpmixininterface(v)) return WrapInterface(static_cast<FPMixinInterfaceValue*>(v)->fpi, v);
    if (v->is_kind_of(class_tag(MAXWrapper))) {
        if (ReferenceTarget* ref = static_cast<MAXWrapper*>(v)->get_max_object()) return WrapAnim(ref);
    }
    return Shown(v);
}

// ── Function-publishing interfaces ───────────────────────────────

static FPInterface* ResolveInterface(const InterfaceRef& r) {
    RequireMain();
    const Interface_ID id(r.a, r.b);
    FPInterface* fpi = nullptr;
    if (r.watch) {
        fpi = r.watch->fpi;
    } else if (r.owner) {
        Animatable* anim = Animatable::GetAnimByHandle(r.owner);
        if (anim && anim->SuperClassID() == BASENODE_CLASS_ID && !InScene(static_cast<INode*>(anim))) anim = nullptr;
        BaseInterface* found = anim ? anim->GetInterface(id) : nullptr;
        fpi = found ? GetFPInterface(found) : nullptr;
    } else if (r.sid) {
        ClassDesc* cd = ClassDirectory::GetInstance().FindClass(r.sid, Class_ID(r.ca, r.cb));
        fpi = cd ? cd->GetInterface(id) : nullptr;
    } else {
        fpi = GetCOREInterface(id);
    }
    if (!fpi || !fpi->GetDesc()) throw NotFound("this interface is gone: its object or plugin was deleted");
    return fpi;
}

static FPInterfaceDesc* DescOf(const InterfaceRef& r) { return ResolveInterface(r)->GetDesc(); }
static std::string InterfaceName(FPInterfaceDesc* d) { return WideToUtf8(d->internal_name.data()); }

static const char* InterfaceKind(const InterfaceRef& r, FPInterfaceDesc* d) {
    if (d->flags & FP_MIXIN) return "mixin";
    return r.sid ? "class" : "core";
}

static Value* InterfaceValue(FPInterface* fpi) {
    if (fpi->GetDesc()->flags & FP_MIXIN) return FPMixinInterfaceValue::intern(fpi);
    return new FPInterfaceValue(fpi);
}

// A returned interface as an Interface: core and class interfaces by ID, mixins
// watched for deletion; any other kind only as its printed form.
static nb::object WrapInterface(FPInterface* fpi, Value* shown) {
    FPInterfaceDesc* d = fpi ? fpi->GetDesc() : nullptr;
    if (!d) return nb::none();
    InterfaceRef r{d->ID.PartA(), d->ID.PartB()};
    if (!(d->flags & FP_MIXIN)) {
        if (GetCOREInterface(d->ID) == fpi) return nb::cast(r);
        if (!d->cd) return Shown(shown);
        r.sid = d->cd->SuperClassID();
        r.ca = d->cd->ClassID().PartA();
        r.cb = d->cd->ClassID().PartB();
        return nb::cast(r);
    }
    // Only interfaces Max reports deleting can be held past this call.
    FPMixinInterface* mixin = GetFPMixinInterface(fpi);
    if (!mixin || fpi->LifetimeControl() != BaseInterface::serverControlled) return Shown(shown);
    auto release = [](InterfaceWatch* w) {
        if (w->registered) w->registered->UnRegisterNotifyCallback(w);
        delete w;
    };
    r.watch = std::shared_ptr<InterfaceWatch>(new InterfaceWatch, release);
    if (!mixin->RegisterNotifyCallback(r.watch.get())) return Shown(shown);
    r.watch->fpi = fpi;
    r.watch->registered = mixin;
    return nb::cast(r);
}

static std::vector<std::string> EnumNames(FPInterfaceDesc* d, EnumID id) {
    std::vector<std::string> names;
    for (int i = 0; id != FP_NO_ENUM && i < d->enumerations.Count(); ++i) {
        FPEnum* e = d->enumerations[i];
        if (!e || e->ID != id) continue;
        for (int j = 0; j < e->enumeration.Count(); ++j) names.push_back(WideToUtf8(e->enumeration[j].name));
    }
    return names;
}

static std::string FpTypeName(FPInterfaceDesc* d, int type, EnumID enumId) {
    const int base = PlainType(type);
    std::string name;
    switch (base) {
    case TYPE_VOID: name = "void"; break;
    case TYPE_NAME: name = "name"; break;
    case TYPE_TSTR: name = "string"; break;
    case TYPE_INTERFACE: name = "interface"; break;
    case TYPE_VALUE: case TYPE_FPVALUE: name = "value"; break;
    case TYPE_BITARRAY: name = "bitarray"; break;
    case TYPE_MATRIX3: name = "matrix3"; break;
    case TYPE_QUAT: name = "quat"; break;
    case TYPE_INTERVAL: name = "interval"; break;
    case TYPE_POINT2: name = "point2"; break;
    case TYPE_COLOR: name = "color"; break;
    case TYPE_bool: name = "bool"; break;
    case TYPE_DOUBLE: name = "double"; break;
    case TYPE_INT64: case TYPE_INTPTR: case TYPE_DWORD: name = "int"; break;
    case TYPE_OBJECT: name = "object"; break;
    case TYPE_CONTROL: name = "controller"; break;
    case TYPE_MESH: name = "mesh"; break;
    case TYPE_BOX3: name = "box3"; break;
    case TYPE_ENUM: {
        const auto names = EnumNames(d, enumId);
        name = "enum";
        for (size_t i = 0; i < names.size(); ++i) name += (i ? "|" : "(") + names[i] + (i + 1 == names.size() ? ")" : "");
        break;
    }
    default:
        name = TypeName(base);
        if (name == "other") name = "type" + std::to_string(base);
    }
    if (type & TYPE_TAB) name += "[]";
    if (type & TYPE_BY_REF) name += "&";
    return name;
}

static bool Keyword(const FPParamDef* p) { return (p->flags & FPP_KEYARG) != 0; }

// "getLayer(which: index) -> interface"; keyword parameters show "= ...".
static std::string Signature(FPInterfaceDesc* d, FPFunctionDef* f) {
    std::string out = WideToUtf8(f->internal_name.data()) + "(";
    for (int i = 0; i < f->params.Count(); ++i) {
        FPParamDef* p = f->params[i];
        if (!p) continue;
        out += (i ? ", " : "") + WideToUtf8(p->internal_name.data()) + ": " + FpTypeName(d, p->type, p->enumID);
        if (Keyword(p)) out += " = ...";
    }
    return out + ") -> " + FpTypeName(d, f->result_type, f->enumID);
}

static FPFunctionDef* FindFunction(FPInterfaceDesc* d, const std::string& name) {
    for (int i = 0; i < d->functions.Count(); ++i)
        if (d->functions[i] && SameName(WideToUtf8(d->functions[i]->internal_name.data()), name)) return d->functions[i];
    return nullptr;
}

static FPPropDef* FindProperty(FPInterfaceDesc* d, const std::string& name) {
    for (int i = 0; i < d->props.Count(); ++i)
        if (d->props[i] && SameName(WideToUtf8(d->props[i]->internal_name.data()), name)) return d->props[i];
    return nullptr;
}

static std::vector<std::string> MemberNames(FPInterfaceDesc* d) {
    std::vector<std::string> names;
    for (int i = 0; i < d->functions.Count(); ++i)
        if (d->functions[i]) names.push_back(WideToUtf8(d->functions[i]->internal_name.data()));
    for (int i = 0; i < d->props.Count(); ++i)
        if (d->props[i]) names.push_back(WideToUtf8(d->props[i]->internal_name.data()));
    return names;
}

[[noreturn]] static void ThrowUnknownMember(FPInterfaceDesc* d, const std::string& name) {
    const auto names = MemberNames(d);
    throw UnknownParam("interface '" + InterfaceName(d) + "' has no function or property '" + name + "'." +
                       Suggest(name, names) + " Members: " + Join(names));
}

static void RequireCallable(const InterfaceRef& r, FPInterfaceDesc* d) {
    if ((d->flags & FP_MIXIN) && !r.owner && !r.watch)
        throw BadParam("'" + InterfaceName(d) + "' is a mixin interface: call it on an object, e.g. node.params.interface('" +
                       InterfaceName(d) + "')");
}

static nb::object CallFunction(const InterfaceRef& r, const std::string& name, nb::handle args, nb::handle kwargs) {
    FPInterface* fpi = ResolveInterface(r);
    FPInterfaceDesc* d = fpi->GetDesc();
    FPFunctionDef* f = FindFunction(d, name);
    if (!f) ThrowUnknownMember(d, name);
    RequireCallable(r, d);
    std::vector<FPParamDef*> positional;
    for (int i = 0; i < f->params.Count(); ++i)
        if (f->params[i] && !Keyword(f->params[i])) positional.push_back(f->params[i]);
    const size_t given = nb::len(args);
    if (!(f->flags & FP_VAR_ARGS) && given != positional.size())
        throw BadParam(Signature(d, f) + " takes " + std::to_string(positional.size()) + " positional argument" +
                       (positional.size() == 1 ? "" : "s") + ", got " + std::to_string(given));
    const size_t keys = kwargs.is_none() ? 0 : nb::len(kwargs);
    const std::string signature = Signature(d, f);
    return InMaxScript(signature, [&](MAXScript_TLS* _tls) {
        const int count = (int)(given + (keys ? 1 + 2 * keys : 0));
        Value** slots = nullptr;
        value_local_array_tls(slots, count + 2);
        slots[0] = InterfaceValue(fpi);
        slots[1] = slots[0]->_get_property(Name::intern(f->internal_name.data()));
        Value** argv = slots + 2;
        int i = 0;
        for (nb::handle arg : args) {
            const int type = (size_t)i < positional.size() ? positional[i]->type : TYPE_VALUE;
            const std::string what = (size_t)i < positional.size() ? WideToUtf8(positional[i]->internal_name.data()) : "argument";
            ToValue(argv[i], arg, type, what);
            ++i;
        }
        if (keys) {
            argv[i++] = &keyarg_marker;
            for (auto [key, value] : nb::borrow<nb::dict>(kwargs)) {
                const std::string keyName = nb::cast<std::string>(key);
                int type = TYPE_VALUE;
                for (int p = 0; p < f->params.Count(); ++p)
                    if (f->params[p] && SameName(WideToUtf8(f->params[p]->internal_name.data()), keyName)) type = f->params[p]->type;
                argv[i++] = Name::intern(Utf8ToWide(keyName).c_str());
                ToValue(argv[i], value, type, keyName);
                ++i;
            }
        }
        slots[0] = slots[1]->apply(argv, count);
        return FromValue(slots[0]);
    });
}

static nb::object GetInterfaceProperty(const InterfaceRef& r, FPInterface* fpi, FPPropDef* prop) {
    RequireCallable(r, fpi->GetDesc());
    return InMaxScript(InterfaceName(fpi->GetDesc()) + "." + WideToUtf8(prop->internal_name.data()), [&](MAXScript_TLS* _tls) {
        Value** slots = nullptr;
        value_local_array_tls(slots, 2);
        slots[0] = InterfaceValue(fpi);
        slots[1] = slots[0]->_get_property(Name::intern(prop->internal_name.data()));
        return FromValue(slots[1]);
    });
}

static void SetInterfaceProperty(const InterfaceRef& r, const std::string& name, nb::handle value) {
    FPInterface* fpi = ResolveInterface(r);
    FPInterfaceDesc* d = fpi->GetDesc();
    FPPropDef* prop = FindProperty(d, name);
    if (!prop) ThrowUnknownMember(d, name);
    if (prop->setter_ID == FP_NO_FUNCTION) throw BadParam(InterfaceName(d) + "." + name + " is read-only");
    RequireCallable(r, d);
    InMaxScript(InterfaceName(d) + "." + name, [&](MAXScript_TLS* _tls) {
        Value** slots = nullptr;
        value_local_array_tls(slots, 2);
        slots[0] = InterfaceValue(fpi);
        ToValue(slots[1], value, prop->prop_type, name);
        slots[0]->_set_property(Name::intern(prop->internal_name.data()), slots[1]);
        return nb::object(nb::none());
    });
}

static nb::object InterfaceAttr(const InterfaceRef& r, const std::string& name) {
    if (name.rfind("__", 0) == 0) throw nb::attribute_error(name.c_str());
    FPInterface* fpi = ResolveInterface(r);
    FPInterfaceDesc* d = fpi->GetDesc();
    if (FPPropDef* prop = FindProperty(d, name)) return GetInterfaceProperty(r, fpi, prop);
    FPFunctionDef* f = FindFunction(d, name);
    if (!f) ThrowUnknownMember(d, name);
    const std::string fn = WideToUtf8(f->internal_name.data());
    const std::string signature = Signature(d, f);
    return nb::cpp_function([r, fn](nb::args args, nb::kwargs kwargs) { return CallFunction(r, fn, args, kwargs); },
                            nb::name(fn.c_str()), signature.c_str());
}

static std::vector<std::string> DescribeInterface(const InterfaceRef& r) {
    FPInterfaceDesc* d = DescOf(r);
    std::vector<std::string> lines{InterfaceName(d) + ": " + InterfaceKind(r, d) + " interface; call functions as "
                                   "iface.name(args), read and set properties as attributes"};
    for (int i = 0; i < d->props.Count(); ++i) {
        FPPropDef* p = d->props[i];
        if (!p) continue;
        lines.push_back(WideToUtf8(p->internal_name.data()) + ": " + FpTypeName(d, p->prop_type, p->enumID) +
                        (p->setter_ID == FP_NO_FUNCTION ? " read-only" : ""));
    }
    for (int i = 0; i < d->functions.Count(); ++i)
        if (d->functions[i]) lines.push_back(Signature(d, d->functions[i]));
    return lines;
}

static std::string InterfaceRepr(const InterfaceRef& r) {
    try {
        FPInterfaceDesc* d = DescOf(r);
        std::string where;
        if (r.owner) {
            Animatable* anim = Animatable::GetAnimByHandle(r.owner);
            where = anim && anim->SuperClassID() == BASENODE_CLASS_ID ? " on '" + NodeName(static_cast<INode*>(anim)) + "'"
                                                                       : " on " + HandlerHelpers::MaxScriptVisibleClassName(anim);
        }
        return "<Interface '" + InterfaceName(d) + "' " + InterfaceKind(r, d) + where + ", " +
               std::to_string(d->functions.Count()) + " functions, " + std::to_string(d->props.Count()) + " properties>";
    } catch (const MaxError&) {
        return "<Interface gone>";
    }
}

static std::vector<InterfaceRef> CoreInterfaces(const std::optional<std::string>& pattern) {
    RequireMain();
    std::vector<std::pair<std::string, InterfaceRef>> rows;
    for (int i = 0; i < NumCOREInterfaces(); ++i) {
        FPInterface* fpi = GetCOREInterfaceAt(i);
        FPInterfaceDesc* d = fpi ? fpi->GetDesc() : nullptr;
        if (!d || (d->flags & (FP_MIXIN | FP_TEST_INTERFACE))) continue;
        const std::string name = InterfaceName(d);
        if (Matches(pattern, {name})) rows.push_back({Lower(name), InterfaceRef{d->ID.PartA(), d->ID.PartB()}});
    }
    std::sort(rows.begin(), rows.end(), [](const auto& x, const auto& y) { return x.first < y.first; });
    std::vector<InterfaceRef> out;
    for (const auto& row : rows) out.push_back(row.second);
    return out;
}

// A core interface, else a static interface of a loaded plugin class.
static InterfaceRef FindInterface(const std::string& name) {
    std::vector<std::string> names;
    for (const InterfaceRef& r : CoreInterfaces(std::nullopt)) {
        const std::string found = InterfaceName(DescOf(r));
        if (SameName(found, name)) return r;
        names.push_back(found);
    }
    DllDir& dir = DllDir::GetInstance();
    for (int dll = 0; dll < dir.Count(); ++dll) {
        if (!dir[dll].IsLoaded()) continue;
        for (int c = 0; c < dir[dll].NumberOfClasses(); ++c) {
            ClassDesc* cd = dir[dll][c];
            for (int i = 0; cd && i < cd->NumInterfaces(); ++i) {
                FPInterface* fpi = cd->GetInterfaceAt(i);
                FPInterfaceDesc* d = fpi ? fpi->GetDesc() : nullptr;
                if (d && !(d->flags & (FP_MIXIN | FP_TEST_INTERFACE)) && SameName(InterfaceName(d), name))
                    return InterfaceRef{d->ID.PartA(), d->ID.PartB(), cd->SuperClassID(), cd->ClassID().PartA(), cd->ClassID().PartB()};
            }
        }
    }
    throw NotFound("no interface '" + name + "'." + Suggest(name, names) +
                   " interfaces() lists the core ones; mixins are on objects: node.interfaces, params.interfaces");
}

// The mixin interfaces an object answers: those its class publishes and the
// core-registered ones (node properties, multi-output maps, ...).
static std::vector<InterfaceRef> Mixins(Animatable* owner) {
    std::vector<InterfaceRef> out;
    std::set<std::pair<ULONG, ULONG>> seen;
    const AnimHandle handle = Animatable::GetHandleByAnim(owner);
    auto offer = [&](FPInterface* fpi) {
        FPInterfaceDesc* d = fpi ? fpi->GetDesc() : nullptr;
        if (!d || !(d->flags & FP_MIXIN) || (d->flags & FP_TEST_INTERFACE)) return;
        if (!seen.insert({d->ID.PartA(), d->ID.PartB()}).second) return;
        // A node answers interface IDs it lacks with its own INode mixin: keep
        // only an interface that is the one asked for.
        BaseInterface* found = owner->GetInterface(d->ID);
        FPInterface* fp = found ? GetFPInterface(found) : nullptr;
        if (fp && fp->GetID() == d->ID) out.push_back(InterfaceRef{d->ID.PartA(), d->ID.PartB(), 0, 0, 0, handle});
    };
    if (ClassDesc* cd = ClassDirectory::GetInstance().FindClass(owner->SuperClassID(), owner->ClassID()))
        for (int i = 0; i < cd->NumInterfaces(); ++i) offer(cd->GetInterfaceAt(i));
    for (int i = 0; i < NumCOREInterfaces(); ++i) offer(GetCOREInterfaceAt(i));
    return out;
}

static InterfaceRef FindMixin(Animatable* owner, const std::string& name) {
    std::vector<std::string> names;
    for (const InterfaceRef& r : Mixins(owner)) {
        const std::string found = InterfaceName(DescOf(r));
        if (SameName(found, name)) return r;
        names.push_back(found);
    }
    const std::string what = HandlerHelpers::MaxScriptVisibleClassName(owner);
    if (names.empty()) throw NotFound(what + " has no mixin interfaces");
    throw NotFound(what + " has no interface '" + name + "'." + Suggest(name, names) + " Interfaces: " + Join(names));
}

static std::vector<InterfaceRef> ClassInterfaces(const ClassInfo& c) {
    ClassDesc* cd = Descriptor(c);
    std::vector<InterfaceRef> out;
    for (int i = 0; i < cd->NumInterfaces(); ++i) {
        FPInterface* fpi = cd->GetInterfaceAt(i);
        FPInterfaceDesc* d = fpi ? fpi->GetDesc() : nullptr;
        if (d && !(d->flags & FP_TEST_INTERFACE)) out.push_back(InterfaceRef{d->ID.PartA(), d->ID.PartB(), c.sid, c.a, c.b});
    }
    return out;
}

// ── Schemas and plugin patches ───────────────────────────────────
// native:plugin_inspect reports and native:plugin_patch takes internal units
// (radians, fractions, ticks); _mx shows and takes MAXScript's.

static double UnitScale(const std::string& type) {
    const std::string base = type.substr(0, type.find('['));
    if (base == "angle") return 180.0 / PI;
    if (base == "percent") return 100.0;
    if (base == "time") return 1.0 / GetTicksPerFrame();
    return 1.0;
}

static json Scaled(const json& v, double k) {
    if (k == 1.0) return v;
    if (v.is_number()) return v.get<double>() * k;
    if ((v.is_array)()) {
        json out = json::array();
        for (const json& x : v) out.push_back(Scaled(x, k));
        return out;
    }
    return v;
}

static void ToScriptUnits(json& property) {
    const double k = UnitScale(property.value("type", ""));
    if (k == 1.0) return;
    for (const char* key : {"value", "default", "element_default", "range"})
        if (property.contains(key)) property[key] = Scaled(property[key], k);
    static const std::pair<const char*, const char*> units[] = {{"radians", "degrees"}, {"fraction", "percent"}, {"ticks", "frames"}};
    for (const auto& [from, to] : units)
        if (property.value("unit", json()) == from) property["unit"] = to;
}

static void Annotate(const json& identity, json& property) {
    if (property.value("type", "") != "int" || !identity.contains("class_id") || !property.contains("property_ref")) return;
    const json& ref = property["property_ref"];
    const ParamCodec::ProviderDomain* d = ParamCodec::FindProviderDomain(
        (SClass_ID)identity.value("superclass_id", 0ull),
        Class_ID(identity["class_id"][0].get<ULONG>(), identity["class_id"][1].get<ULONG>()),
        ref.value("block_id", -1), ref.value("param_id", -1), property.value("name", ""));
    if (!d) return;
    json choices = json::array();
    for (const auto& [label, value] : d->choices) choices.push_back({{"name", label}, {"value", value}});
    property["domain"] = {{"kind", "enum"}, {"choices", choices}, {"complete", d->complete},
                          {"source", "provider_reference"}, {"evidence", d->evidence}};
}

static Animatable* AnimFromRef(const json& ref) {
    if (!ref.is_object() || !ref.contains("handle")) return nullptr;
    const json& handle = ref["handle"];
    try {
        const unsigned long long h = (handle.is_string)() ? std::stoull(handle.get<std::string>()) : handle.get<unsigned long long>();
        return Animatable::GetAnimByHandle((AnimHandle)h);
    } catch (const std::exception&) {
        return nullptr;
    }
}

// Owner references (dicts with session, handle and class_ref) as objects.
static nb::object WrapRefs(nb::object v) {
    if (nb::isinstance<nb::dict>(v)) {
        const json ref = FromPython(v);
        if (ref.contains("session") && ref.contains("class_ref")) return WrapAnim(AnimFromRef(ref));
        return v;
    }
    if (nb::isinstance<nb::list>(v)) {
        nb::list out;
        for (nb::handle item : v) out.append(WrapRefs(nb::borrow(item)));
        return out;
    }
    return v;
}

static nb::object SchemaToPython(json schema) {
    schema.erase("interfaces");   // .interfaces lists them as Interface objects
    const json identity = schema.value("identity", json::object());
    if (schema.contains("time")) schema["time"] = TicksToFrame(schema["time"].get<int>());
    if (!schema.contains("properties")) throw MaxError("the plugin inspector returned no properties");
    for (json& property : schema["properties"]) {
        Annotate(identity, property);
        ToScriptUnits(property);
    }
    nb::object out = ToPython(schema);
    nb::object properties = out["properties"];
    for (nb::handle property : properties) {
        nb::dict p = nb::borrow<nb::dict>(property);
        for (const char* key : {"value", "controller_ref"})
            if (p.contains(key)) p[key] = WrapRefs(p[key]);
    }
    return out;
}

static json SessionRef(Animatable* anim, const std::string& session) {
    return {{"session", session},
            {"handle", std::to_string((unsigned long long)Animatable::GetHandleByAnim(anim))},
            {"class_ref", {{"superclass_id", (uint64_t)anim->SuperClassID()},
                           {"class_id", {(uint32_t)anim->ClassID().PartA(), (uint32_t)anim->ClassID().PartB()}}}}};
}

// A node-scoped reference for base objects and stack modifiers (re-resolved by
// the patch, so a replaced object or reordered stack is refused), else a
// session reference.
static json OwnerRefFor(const Params& p, Animatable* owner) {
    const json node = {{"handle", (unsigned long long)p.node}};
    if (!p.owner) return {{"node", node}, {"scope", "base_object"}};
    if (p.node) {
        const std::vector<ModifierRef> mods = NodeModifiers(Node{p.node});
        for (size_t i = 0; i < mods.size(); ++i)
            if (mods[i].mod == p.owner) return {{"node", node}, {"scope", "modifier"}, {"modifier_index", i + 1}};
    }
    return SessionRef(owner, NativeJson("native:lighting_context", json::object()).value("session", ""));
}

static json PropertyRef(int block, int param) { return {{"route", "pb2"}, {"block_id", block}, {"param_id", param}}; }

static json OwnerFields(Animatable* owner, nb::handle fields) {
    json out = json::array();
    for (nb::handle field : fields) {
        const std::string name = nb::cast<std::string>(field);
        const std::optional<ParamRef> ref = FindParam(owner, name);
        if (!ref)
            throw UnknownParam(HandlerHelpers::MaxScriptVisibleClassName(owner) + " has no ParamBlock2 parameter '" + name + "'." +
                               Suggest(name, ParamNames(owner)));
        out.push_back(PropertyRef(ref->block->ID(), ref->def->ID));
    }
    return out;
}

static json ClassFields(const ClassInfo& c, nb::handle fields) {
    ClassDesc* cd = Descriptor(c);
    json out = json::array();
    for (nb::handle field : fields) {
        const std::string name = nb::cast<std::string>(field);
        std::vector<std::string> names;
        json found;
        for (int b = 0; b < cd->NumParamBlockDescs() && found.is_null(); ++b) {
            ParamBlockDesc2* desc = cd->GetParamBlockDesc(b);
            for (int i = 0; desc && i < desc->count; ++i) {
                const ParamDef& def = desc->GetParamDef(desc->IndextoID(i));
                if (!def.int_name || !*def.int_name) continue;
                names.push_back(WideToUtf8(def.int_name));
                if (SameName(names.back(), name)) { found = PropertyRef(desc->ID, def.ID); break; }
            }
        }
        if (found.is_null()) throw UnknownParam("class has no ParamBlock2 parameter '" + name + "'." + Suggest(name, names));
        out.push_back(found);
    }
    return out;
}

static nb::object Inspect(json request, const std::string& query, int offset, int limit) {
    request["query"] = query;
    request["offset"] = offset;
    request["limit"] = limit;
    return SchemaToPython(NativeJson("native:plugin_inspect", request));
}

static nb::object OwnerSchema(const Params& p, nb::args fields, const std::string& query, int offset, int limit) {
    Animatable* owner = ParamOwner(p);
    return Inspect({{"owner_ref", OwnerRefFor(p, owner)}, {"fields", OwnerFields(owner, fields)}}, query, offset, limit);
}

static nb::object ClassSchema(const ClassInfo& c, nb::args fields, const std::string& query, int offset, int limit) {
    const json classRef = {{"superclass_id", (uint64_t)c.sid}, {"class_id", {(uint32_t)c.a, (uint32_t)c.b}}};
    return Inspect({{"class_ref", classRef}, {"fields", ClassFields(c, fields)}}, query, offset, limit);
}

// What a patch needs from a schema() report.
struct SchemaView {
    json owner_ref, schema_token, state_token, properties;   // properties: name, property_ref, type, domain
    std::string session;
    Animatable* owner = nullptr;
};

static SchemaView ReadSchema(nb::handle schema) {
    if (!nb::isinstance<nb::dict>(schema) || !nb::borrow<nb::dict>(schema).contains("state_token"))
        throw BadParam("pass the dict that params.schema(...) returned");
    nb::dict s = nb::borrow<nb::dict>(schema);
    SchemaView view;
    view.owner_ref = FromPython(nb::object(s["owner_ref"]));
    view.schema_token = FromPython(nb::object(s["schema_token"]));
    view.state_token = FromPython(nb::object(s["state_token"]));
    view.session = view.owner_ref.value("session", "");
    view.owner = AnimFromRef(view.owner_ref);
    if (!view.owner) throw NotFound("the object this schema describes was deleted");
    view.properties = json::array();
    for (nb::handle item : nb::object(s["properties"])) {
        nb::dict p = nb::borrow<nb::dict>(item);
        auto field = [&](const char* key) { return p.contains(key) ? FromPython(nb::object(p[key])) : json(); };
        view.properties.push_back({{"name", field("name")}, {"property_ref", field("property_ref")},
                                   {"type", field("type")}, {"domain", field("domain")}});
    }
    return view;
}

// The schema property `name` names: its parameter name, or a MAXScript alias.
static const json& SchemaProperty(const SchemaView& view, const std::string& name) {
    for (const json& p : view.properties)
        if (SameName(p.value("name", ""), name)) return p;
    if (const std::optional<ParamRef> ref = FindParam(view.owner, name)) {
        const json wanted = PropertyRef(ref->block->ID(), ref->def->ID);
        for (const json& p : view.properties)
            if (p["property_ref"] == wanted) return p;
    }
    throw BadParam("'" + name + "' is not in this schema; inspect it first: params.schema('" + name + "')");
}

static json EnumValue(const std::string& choice, const json& domain, const std::string& what) {
    const json choices = domain.is_object() ? domain.value("choices", json()) : json();
    if (!(choices.is_array)()) throw BadParam(what + " publishes no named choices; pass its number");
    std::vector<std::string> names;
    for (const json& c : choices) {
        names.push_back(c.value("name", ""));
        if (names.back() == choice) return c.value("value", json());
    }
    throw BadParam("unknown choice '" + choice + "' for " + what + "; choices: " + Join(names));
}

// A value in MAXScript units as the patch handler's typed JSON.
static json PatchValue(nb::handle v, const std::string& type, const json& domain, const std::string& session, const std::string& what) {
    const bool array = type.size() > 2 && type.compare(type.size() - 2, 2, "[]") == 0;
    const std::string base = array ? type.substr(0, type.size() - 2) : type;
    if (array) {
        if (!nb::isinstance<nb::list>(v) && !nb::isinstance<nb::tuple>(v))
            throw BadParam(what + " is an array; pass a list with its current element count");
        json out = json::array();
        for (size_t i = 0; i < nb::len(v); ++i)
            out.push_back(PatchValue(nb::object(v[i]), base, domain, session, what + "[" + std::to_string(i) + "]"));
        return out;
    }
    try {
        if (v.is_none()) return nullptr;
        if (Animatable* anim = AnimOf(v)) return SessionRef(anim, session);
        if (nb::isinstance<nb::dict>(v)) return FromPython(v);
        if (nb::isinstance<nb::str>(v) && base == "int") return EnumValue(nb::cast<std::string>(v), domain, what);
        if (nb::isinstance<nb::bool_>(v)) return nb::cast<bool>(v);
        const bool number = nb::isinstance<nb::int_>(v) || nb::isinstance<nb::float_>(v);
        if (number && base == "time") return (long long)FrameToTicks(nb::cast<double>(v));
        if (number && UnitScale(base) != 1.0) return nb::cast<double>(v) / UnitScale(base);
        return FromPython(v);
    } catch (const nb::cast_error&) {
        throw BadParam(what + " cannot take " + ReprOf(v));
    }
}

static nb::dict PatchReport(const json& result, const std::vector<SchemaView>& views) {
    nb::list properties;
    for (const json& proof : result.value("properties", json::array())) {
        Animatable* owner = AnimFromRef(proof.value("owner_ref", json()));
        const json ref = proof.value("property_ref", json());
        std::string name, type;
        for (const SchemaView& view : views)
            for (const json& p : view.properties)
                if (view.owner == owner && p["property_ref"] == ref) { name = p.value("name", ""); type = p.value("type", ""); }
        const double k = UnitScale(type);
        nb::dict row;
        row["owner"] = WrapAnim(owner);
        row["name"] = name;
        row["before"] = WrapRefs(ToPython(Scaled(proof.value("before", json()), k)));
        row["value"] = WrapRefs(ToPython(Scaled(proof.value("actual", json()), k)));
        properties.append(row);
    }
    nb::dict out;
    out["status"] = result.value("status", "");
    out["verification"] = result.value("verification", "");
    out["transaction"] = result.value("transaction", "");
    out["properties"] = properties;
    return out;
}

// edits: (schema, {name: value}) pairs; one guarded, verified undo step.
static nb::dict PluginPatch(nb::handle edits, bool allInstances) {
    std::vector<SchemaView> views;
    json planned = json::array();
    for (nb::handle edit : edits) {
        const bool pair = (nb::isinstance<nb::tuple>(edit) || nb::isinstance<nb::list>(edit)) && nb::len(edit) == 2;
        nb::object values = pair ? nb::object(edit[1]) : nb::object(nb::none());
        if (!nb::isinstance<nb::dict>(values))
            throw BadParam("each edit is (schema, {name: value}), with schema from params.schema(...)");
        SchemaView view = ReadSchema(nb::object(edit[0]));
        for (auto [key, value] : nb::borrow<nb::dict>(values)) {
            const std::string name = nb::cast<std::string>(key);
            const json& property = SchemaProperty(view, name);
            json item = {{"owner_ref", view.owner_ref}, {"property_ref", property["property_ref"]},
                         {"expected_schema", view.schema_token}, {"expected_state", view.state_token},
                         {"value", PatchValue(value, property.value("type", ""), property["domain"], view.session, name)}};
            if (allInstances) item["sharing"] = "all_instances";
            planned.push_back(item);
        }
        views.push_back(std::move(view));
    }
    if (planned.empty()) throw BadParam("nothing to patch: give each schema the values to set");
    json result;
    try {
        result = NativeJson("native:plugin_patch", {{"version", 1}, {"edits", planned}});
    } catch (const MaxError& e) {
        const std::string message = e.what();
        if (message.rfind("[STALE_", 0) == 0) throw MaxError(message + " Read .schema(...) again and patch with the new report.");
        throw;
    }
    return PatchReport(result, views);
}

// ── Node inspection ──────────────────────────────────────────────

static std::vector<std::string> PluginFiles(const Node& n, INode* node) {
    std::vector<std::string> files;
    auto add = [&](Animatable* anim) {
        ClassEntry* entry = anim ? ClassDirectory::GetInstance().FindClassEntry(anim->SuperClassID(), anim->ClassID()) : nullptr;
        const std::string file = entry ? DllFile(entry->DllNumber()) : std::string();
        if (!file.empty() && std::find(files.begin(), files.end(), file) == files.end()) files.push_back(file);
    };
    add(BaseObject(node));
    for (const ModifierRef& mod : NodeModifiers(n)) add(Animatable::GetAnimByHandle(mod.mod));
    add(node->GetMtl());
    return files;
}

static nb::dict InspectNode(const Node& n) {
    INode* node = Resolve(n);
    nb::object self = nb::cast(n);
    ObjectState os = node->EvalWorldState(Now());
    nb::dict out;
    out["name"] = NodeName(node);
    out["handle"] = (unsigned long long)n.handle;
    out["class"] = os.obj ? HandlerHelpers::MaxScriptVisibleClassName(os.obj) : std::string();
    {
        ScopedMaxScriptEvaluationContext context;
        out["superclass"] = os.obj ? SuperclassName(os.obj->SuperClassID()) : std::string();
    }
    out["base_class"] = ClassOf(node);
    for (const char* key : {"pos", "rot", "scale", "parent", "children", "layer", "hidden"}) out[key] = nb::object(self.attr(key));
    out["frozen"] = node->IsFrozen() != 0;
    out["renderable"] = node->Renderable() != 0;
    out["primary_visibility"] = node->GetPrimaryVisibility() != 0;
    out["secondary_visibility"] = node->GetSecondaryVisibility() != 0;
    out["cast_shadows"] = node->CastShadows() != 0;
    out["receive_shadows"] = node->RcvShadows() != 0;
    const DWORD wire = node->GetWireColor();
    out["wirecolor"] = nb::make_tuple(GetRValue(wire) / 255.0, GetGValue(wire) / 255.0, GetBValue(wire) / 255.0);
    INodeTab instances;
    IInstanceMgr::GetInstanceMgr()->GetInstances(*node, instances);
    std::vector<Node> shared;
    for (int i = 0; i < instances.Count(); ++i)
        if (InScene(instances[i])) shared.push_back(Wrap(instances[i]));
    out["instances"] = shared;
    out["mesh"] = nb::object(self.attr("mesh_counts"));
    out["bbox"] = nb::object(self.attr("bbox"));
    out["modifiers"] = nb::object(self.attr("modifiers"));
    out["material"] = nb::object(self.attr("material"));
    out["plugins"] = PluginFiles(n, node);
    return out;
}

// ── OSL shaders ──────────────────────────────────────────────────

// A .osl path, or the name of a shader in 3ds Max's OSL folder.
static std::wstring OslPath(const std::string& shader) {
    std::wstring path = Utf8ToWide(shader);
    if (path.find_first_of(L"\\/") == std::wstring::npos) {
        std::wstring root = GetCOREInterface()->GetDir(APP_MAX_SYS_ROOT_DIR).data();
        if (!root.empty() && root.back() != L'\\') root += L'\\';
        path = root + L"OSL\\" + path;
        if (path.size() < 4 || _wcsicmp(path.c_str() + path.size() - 4, L".osl") != 0) path += L".osl";
    }
    if (GetFileAttributesW(path.c_str()) == INVALID_FILE_ATTRIBUTES)
        throw NotFound("no OSL shader at " + WideToUtf8(path.c_str()) + "; pass a .osl path or the name of a shader in 3ds Max's OSL folder");
    return path;
}

// (name, type) of each output of a multi-output map, through the mixin that
// publishes numIMultipleOutputChannels.
static nb::list OutputChannels(Animatable* map) {
    nb::list out;
    for (const InterfaceRef& channels : Mixins(map)) {
        FPInterface* fpi = ResolveInterface(channels);
        FPPropDef* count = FindProperty(fpi->GetDesc(), "numIMultipleOutputChannels");
        if (!count) continue;
        const nb::object n = GetInterfaceProperty(channels, fpi, count);
        for (int i = 1; nb::isinstance<nb::int_>(n) && i <= nb::cast<int>(n); ++i) {
            const nb::tuple index = nb::make_tuple(i);
            out.append(nb::make_tuple(CallFunction(channels, "getIMultipleOutputChannelName", index, nb::none()),
                                      CallFunction(channels, "getIMultipleOutputChannelType", index, nb::none())));
        }
        break;
    }
    return out;
}

static nb::dict OslInfo(const std::string& shader) {
    RequireMain();
    const std::wstring path = OslPath(shader);
    ClassDesc* cd = FindClass("OSLMap", {TEXMAP_CLASS_ID});
    if (!cd) throw NotFound("this 3ds Max has no OSLMap class");
    auto* created = static_cast<Animatable*>(GetCOREInterface()->CreateInstance(TEXMAP_CLASS_ID, cd->ClassID()));
    if (!created || created->SuperClassID() != TEXMAP_CLASS_ID) {
        if (created) created->DeleteThis();
        throw MaxError("3ds Max could not create an OSLMap");
    }
    // The map is temporary: nothing references it once this returns.
    struct Discard {
        Texmap* map;
        ~Discard() { map->MaybeAutoDelete(); }
    } discard{static_cast<Texmap*>(created)};
    NoAnimate noAnimate;
    const TimeValue t = Now();
    SetParam(created, "OSLPath", nb::str(WideToUtf8(path.c_str()).c_str()), t);
    SetParam(created, "OSLAutoUpdate", nb::bool_(true), t);
    const nb::object shaderName = GetValue(created, "OSLShaderName", t);
    const std::string compiled = nb::isinstance<nb::str>(shaderName) ? nb::cast<std::string>(shaderName) : std::string();
    // OSLMap keeps its built-in "Example" shader when a file does not compile.
    if (compiled.empty() || compiled == "Example")
        throw BadParam("the OSL shader at " + WideToUtf8(path.c_str()) + " did not compile; check its syntax and that its shader name matches the file");
    nb::dict out;
    out["shader"] = compiled;
    out["path"] = WideToUtf8(path.c_str());
    out["params"] = nb::cast(Params{0, Animatable::GetHandleByAnim(created)}).attr("describe")();
    out["outputs"] = OutputChannels(created);
    return out;
}

// ── Capabilities ─────────────────────────────────────────────────

static nb::dict Capabilities() {
    const json c = NativeJson("native:get_plugin_capabilities", json::object());
    const json found = c.value("plugins", json::object());
    nb::dict plugins;
    static const std::pair<const char*, const char*> keys[] = {
        {"forestPack", "forest_pack"}, {"forestLite", "forest_lite"}, {"tyFlow", "tyflow"},
        {"railClone", "railclone"}, {"phoenixFD", "phoenix_fd"}};
    for (const auto& [from, to] : keys) plugins[to] = ToPython(found.value(from, json(false)));
    nb::dict out;
    out["max_version"] = ToPython(c.value("maxVersion", json()));
    out["renderer"] = ToPython(c.value("renderer", json()));
    out["renderers"] = ToPython(c.value("renderers", json::array()));
    out["plugins"] = plugins;
    out["material_classes"] = ToPython(c.value("materialClasses", json()));
    out["geometry_classes"] = ToPython(c.value("geometryClasses", json()));
    out["modifier_classes"] = ToPython(c.value("modifierClasses", json()));
    return out;
}

// ── Macroscripts ─────────────────────────────────────────────────

struct MacroRef { int id = -1; };   // MacroID

static MacroEntry* ResolveMacro(const MacroRef& m) {
    RequireMain();
    MacroDir& dir = GetMacroScriptDir();
    const MacroID id = static_cast<MacroID>(m.id);
    MacroEntry* entry = dir.ValidID(id) ? dir.GetMacro(id) : nullptr;
    if (!entry) throw NotFound("macroscript #" + std::to_string(m.id) + " is no longer defined");
    return entry;
}

static std::string Text(const MSTR& text) { return WideToUtf8(text.data()); }

static std::vector<MacroRef> Macros(const std::optional<std::string>& pattern, const std::optional<std::string>& category) {
    RequireMain();
    MacroDir& dir = GetMacroScriptDir();
    std::vector<MacroRef> out;
    for (int i = 0; i < dir.Count(); ++i) {
        MacroEntry* e = dir.GetMacro(i);
        if (e && Matches(category, {Text(e->GetCategory())}) &&
            Matches(pattern, {Text(e->GetName()), Text(e->GetToolTip()), Text(e->GetButtonText())}))
            out.push_back(MacroRef{e->GetID()});
    }
    return out;
}

static MacroRef FindMacro(const std::string& name, const std::optional<std::string>& category) {
    RequireMain();
    MacroDir& dir = GetMacroScriptDir();
    std::vector<std::string> names, categories;
    std::vector<MacroRef> found;
    for (int i = 0; i < dir.Count(); ++i) {
        MacroEntry* e = dir.GetMacro(i);
        if (!e) continue;
        names.push_back(Text(e->GetName()));
        if (SameName(names.back(), name) && (!category || SameName(Text(e->GetCategory()), *category))) {
            found.push_back(MacroRef{e->GetID()});
            categories.push_back(Text(e->GetCategory()));
        }
    }
    if (found.empty()) throw NotFound("no macroscript '" + name + "'." + Suggest(name, names));
    if (found.size() > 1)
        throw Ambiguous(std::to_string(found.size()) + " macroscripts are named '" + name + "' (categories " + Join(categories) +
                        "); pass category=");
    return found.front();
}

static nb::object RunMacro(const MacroRef& m) {
    MacroEntry* entry = ResolveMacro(m);
    return InMaxScript("macroscript " + Text(entry->GetName()), [&](MAXScript_TLS* _tls) {
        Value** slots = nullptr;
        value_local_array_tls(slots, 1);
        slots[0] = entry->Execute();
        return FromValue(slots[0]);
    });
}

static std::string MacroRepr(const MacroRef& m) {
    try {
        MacroEntry* e = ResolveMacro(m);
        return "<Macro '" + Text(e->GetName()) + "' in '" + Text(e->GetCategory()) + "'>";
    } catch (const MaxError&) {
        return "<Macro #" + std::to_string(m.id) + " removed>";
    }
}

// ── Action tables ────────────────────────────────────────────────

struct ActionTableRef { unsigned long id = 0; };       // ActionTableId
struct ActionRef { unsigned long table = 0; int id = 0; };

static IActionManager* ActionManager() {
    RequireMain();
    IActionManager* manager = GetCOREInterface()->GetActionManager();
    if (!manager) throw MaxError("3ds Max's action manager is not available");
    return manager;
}

static ActionTable* ResolveTable(unsigned long id) {
    ActionTable* table = ActionManager()->FindTable((ActionTableId)id);
    if (!table) throw NotFound("action table " + std::to_string(id) + " is no longer registered");
    return table;
}

static ActionItem* ResolveAction(const ActionRef& a) {
    ActionTable* table = ResolveTable(a.table);
    ActionItem* item = table->GetAction(a.id);
    if (!item) throw NotFound("action " + std::to_string(a.id) + " is no longer in '" + Text(table->GetName()) + "'");
    return item;
}

// Some plugins' action items crash when asked for their text (seen by the
// bridge's action listing, and caught as structured exceptions under /EHa);
// such text reads as empty.
static std::string ItemText(ActionItem* item, void (ActionItem::*get)(MSTR&)) {
    try {
        MSTR text;
        (item->*get)(text);
        return Text(text);
    } catch (...) {
        return {};
    }
}

static void TableActions(ActionTable* table, const std::optional<std::string>& pattern, std::vector<ActionRef>& out) {
    for (int i = 0; i < table->Count(); ++i) {
        ActionItem* item = table->GetActionByIndex(i);
        if (!item) continue;
        if (!pattern || Matches(pattern, {ItemText(item, &ActionItem::GetMenuText), ItemText(item, &ActionItem::GetDescriptionText),
                                          ItemText(item, &ActionItem::GetCategoryText)}))
            out.push_back(ActionRef{(unsigned long)table->GetId(), item->GetId()});
    }
}

static std::vector<ActionTableRef> ActionTables(const std::optional<std::string>& pattern) {
    IActionManager* manager = ActionManager();
    std::vector<ActionTableRef> out;
    for (int t = 0; t < manager->NumActionTables(); ++t)
        if (ActionTable* table = manager->GetTable(t); table && Matches(pattern, {Text(table->GetName())}))
            out.push_back(ActionTableRef{(unsigned long)table->GetId()});
    return out;
}

static std::vector<ActionRef> Actions(const std::optional<std::string>& pattern, const std::optional<std::string>& tables) {
    std::vector<ActionRef> out;
    for (const ActionTableRef& t : ActionTables(tables)) TableActions(ResolveTable(t.id), pattern, out);
    return out;
}

static std::string ActionText(const ActionRef& a) { return ItemText(ResolveAction(a), &ActionItem::GetMenuText); }

// ── Binding ──────────────────────────────────────────────────────

template <typename T>
static void BindIdentity(nb::class_<T>& cls, bool (*same)(const T&, const T&), Py_hash_t (*hash)(const T&)) {
    cls.def("__eq__", [same](const T& a, nb::handle b) { return nb::isinstance<T>(b) && same(a, nb::cast<T>(b)); }, nb::arg("other").none())
       .def("__hash__", [hash](const T& x) { return hash(x); });
}

// Params lists its names in __dir__; keep this file's additions discoverable.
static void ExtendParamsDir(std::initializer_list<const char*> added) {
    nb::handle cls = nb::type<Params>();
    nb::setattr(cls, "_dir_before_plugins", nb::getattr(cls, "__dir__"));
    std::vector<std::string> names(added.begin(), added.end());
    AddMethod<Params>("__dir__", [names](nb::handle self) {
        nb::list out;
        for (nb::handle name : nb::getattr(self, "_dir_before_plugins")()) out.append(name);
        for (const std::string& name : names) out.append(nb::str(name.c_str()));
        return out;
    });
}

static void BindClasses(nb::module_& m) {
    nb::class_<ClassInfo> cls(m, "ClassInfo", "A plugin class: name, kind, plugin, describe(), interfaces, schema(), nodes.");
    cls.def_prop_ro("name", [](const ClassInfo& c) {
            ClassEntry& entry = Entry(c);
            ScopedMaxScriptEvaluationContext context;
            return ScriptName(c.sid, entry);
        }, "MAXScript name, as create() and classOf take it.")
        .def_prop_ro("label", [](const ClassInfo& c) { return Text(Entry(c).ClassName()); }, "Localized UI name.")
        .def_prop_ro("kind", [](const ClassInfo& c) { Entry(c); ScopedMaxScriptEvaluationContext context; return KindOf(c.sid); },
            "'geometry', 'modifier', 'material', ... or the MAXScript superclass name.")
        .def_prop_ro("superclass", [](const ClassInfo& c) {
            Entry(c);
            ScopedMaxScriptEvaluationContext context;
            return SuperclassName(c.sid);
        }, "MAXScript superclass name: GeometryClass, Modifier, ...")
        .def_prop_ro("superclass_id", [](const ClassInfo& c) { return (unsigned long)c.sid; },
            "The superclass's SClass_ID, like MAXScript's superClassID.")
        .def_prop_ro("class_id", [](const ClassInfo& c) { return std::make_pair((unsigned long)c.a, (unsigned long)c.b); },
            "Class_ID as (a, b), like MAXScript's classID.")
        .def_prop_ro("category", [](const ClassInfo& c) { return Text(Entry(c).Category()); }, "Create-panel category.")
        .def_prop_ro("plugin", [](const ClassInfo& c) -> std::optional<PluginRef> {
            const int index = Entry(c).DllNumber();
            if (index < 0 || index >= DllDir::GetInstance().Count()) return std::nullopt;
            return PluginRef{index};
        }, "The plugin DLL that defines the class, or None.")
        .def_prop_ro("creatable", &Creatable, "Whether scripts can create it (public and not marked no-create).")
        .def_prop_ro("interfaces", &ClassInterfaces, "Interfaces the class publishes; static ones are callable, mixins need an object.")
        .def("describe", &DescribeClass,
            "Header plus one line per parameter with its default, range, {named choices}, \"UI label\" and +anim "
            "(MAXScript units); subanims (gizmo, center) and MAXScript properties after, from a temporary instance.")
        .def("schema", &ClassSchema, "fields"_a, "query"_a = "", "offset"_a = 0, "limit"_a = 256,
            "Plugin inspector metadata: property refs, labels, units, domains, coverage: schema('radius').")
        .def_prop_ro("nodes", [](const ClassInfo& c) {
            return NodesUsing([&](SClass_ID sid, const Class_ID& id) { return sid == c.sid && id == Class_ID(c.a, c.b); });
        }, "Scene nodes whose object, modifiers, material or controllers use the class.")
        .def("__repr__", [](const ClassInfo& c) {
            ClassEntry* entry = ClassDirectory::GetInstance().FindClassEntry(c.sid, Class_ID(c.a, c.b));
            if (!entry) return "<Class (" + Hex(c.a) + ", " + Hex(c.b) + ") unregistered>";
            ScopedMaxScriptEvaluationContext context;
            return "<Class '" + ScriptName(c.sid, *entry) + "' " + KindOf(c.sid) + ">";
        });
    BindIdentity<ClassInfo>(cls, [](const ClassInfo& x, const ClassInfo& y) { return x.sid == y.sid && x.a == y.a && x.b == y.b; },
        [](const ClassInfo& c) { return (Py_hash_t)(((size_t)c.a * 31 + c.b) * 31 + c.sid); });

    nb::class_<PluginRef> plugin(m, "Plugin", "A loaded plugin DLL: name, path, description, classes, nodes.");
    plugin.def_prop_ro("name", [](const PluginRef& p) { return Text(Dll(p).GetFileName()); }, "File name: 'prim.dlo'.")
        .def_prop_ro("path", [](const PluginRef& p) { return Text(Dll(p).GetFullPath()); }, "Full path of the DLL file.")
        .def_prop_ro("description", [](const PluginRef& p) { return WideToUtf8(Dll(p).Description()); },
            "The description the plugin gives of itself.")
        .def_prop_ro("loaded", [](const PluginRef& p) { return Dll(p).IsLoaded(); }, "False while 3ds Max defers loading it.")
        .def_prop_ro("classes", &PluginClasses, "Classes the plugin defines.")
        .def_prop_ro("nodes", [](const PluginRef& p) {
            std::set<std::tuple<SClass_ID, ULONG, ULONG>> ids;
            for (const ClassInfo& c : PluginClasses(p)) ids.insert({c.sid, c.a, c.b});
            return NodesUsing([&](SClass_ID sid, const Class_ID& id) { return ids.count({sid, id.PartA(), id.PartB()}) > 0; });
        }, "Scene nodes that use any of the plugin's classes.")
        .def("__repr__", [](const PluginRef& p) {
            DllDir& dir = DllDir::GetInstance();
            if (p.index < 0 || p.index >= dir.Count()) return std::string("<Plugin unloaded>");
            return "<Plugin '" + Text(dir[p.index].GetFileName()) + "' '" + WideToUtf8(dir[p.index].Description()) + "'>";
        });
    BindIdentity<PluginRef>(plugin, [](const PluginRef& x, const PluginRef& y) { return x.index == y.index; },
        [](const PluginRef& p) { return (Py_hash_t)p.index; });

    m.def("classes", &Classes, "kind"_a = nb::none(), "pattern"_a = nb::none(), "plugin"_a = nb::none(),
          "Registered classes by kind, wildcard name and plugin file: classes('modifier', '*Bend*'), classes(plugin='tyFlow*').");
    m.def("class_info", &FindClassInfo, "name"_a, "kind"_a = nb::none(),
          "A class by MAXScript or UI name: class_info('Bend'); kind narrows a name several kinds share.");
    m.def("plugins", &Plugins, "pattern"_a = nb::none(), "Plugin DLLs by wildcard file name or description: plugins('*corona*').");
    m.def("capabilities", &Capabilities,
          "What this 3ds Max has: its version, the renderer in use and those installed, which known plugins are "
          "installed (plugins: forest_pack, forest_lite, tyflow, railclone, phoenix_fd, as bools) and class counts. "
          "Keys: {'max_version', 'renderer', 'renderers', 'plugins', 'material_classes', 'geometry_classes', "
          "'modifier_classes'}.");
}

static void BindInterfaces(nb::module_& m) {
    nb::class_<InterfaceRef> iface(m, "Interface",
        "A function-publishing interface: iface.fn(args) calls a function, iface.prop reads or sets a property. "
        "Arguments follow MAXScript: degrees, 1-based indices, enums by name.");
    iface.def_prop_ro("name", [](const InterfaceRef& r) { return InterfaceName(DescOf(r)); },
            "The interface's name, as interface(name) takes it.")
        .def_prop_ro("kind", [](const InterfaceRef& r) { return std::string(InterfaceKind(r, DescOf(r))); },
            "'core', 'class' (a class's static interface) or 'mixin' (on an object).")
        .def("describe", &DescribeInterface, "Its properties and function signatures, one per line.")
        .def("call", [](const InterfaceRef& r, const std::string& function, nb::args args, nb::kwargs kwargs) {
            return CallFunction(r, function, args, kwargs);
        }, "function"_a, "args"_a, "kwargs"_a, "Call a function by name: iface.call('getLayer', 1).")
        .def("__getattr__", &InterfaceAttr)
        .def("__setattr__", &SetInterfaceProperty)
        .def("__dir__", [](const InterfaceRef& r) {
            std::vector<std::string> names = MemberNames(DescOf(r));
            for (const char* own : {"call", "describe", "kind", "name"}) names.push_back(own);
            return names;
        })
        .def("__repr__", &InterfaceRepr);
    BindIdentity<InterfaceRef>(iface,
        [](const InterfaceRef& x, const InterfaceRef& y) {
            return x.a == y.a && x.b == y.b && x.sid == y.sid && x.ca == y.ca && x.cb == y.cb && x.owner == y.owner &&
                   (x.watch ? y.watch && x.watch->fpi == y.watch->fpi : !y.watch);
        },
        [](const InterfaceRef& r) { return (Py_hash_t)(((size_t)r.a * 31 + r.b) * 31 + r.owner); });

    m.def("interfaces", &CoreInterfaces, "pattern"_a = nb::none(),
          "Core interfaces (MAXScript's global ones), by wildcard name: interfaces('*Layer*').");
    m.def("interface", &FindInterface, "name"_a, "A core or plugin-class static interface by name: interface('LayerManager').");

    AddProperty<Node>("interfaces", [](const Node& n) { return Mixins(Resolve(n)); }, "Mixin interfaces of the node itself.");
    AddMethod<Node>("interface", [](const Node& n, const std::string& name) { return FindMixin(Resolve(n), name); }, "name"_a,
                    "A mixin interface of the node by name: node.interface('INodeBakeProperties').");
    AddProperty<Params>("interfaces", [](const Params& p) { return Mixins(ParamOwner(p)); },
                        "Mixin interfaces of the object these parameters belong to.");
    AddMethod<Params>("interface", [](const Params& p, const std::string& name) { return FindMixin(ParamOwner(p), name); }, "name"_a,
                      "A mixin interface of the object by name: mod.params.interface('unwrap2').");
}

static void BindTools(nb::module_& m) {
    nb::class_<MacroRef> macro(m, "Macro", "A macroscript: name, category, tooltip, text, file, run().");
    macro.def_prop_ro("name", [](const MacroRef& x) { return Text(ResolveMacro(x)->GetName()); },
            "The macroscript's name, as macro(name) and macros.run take it.")
        .def_prop_ro("category", [](const MacroRef& x) { return Text(ResolveMacro(x)->GetCategory()); },
            "The category it is filed under.")
        .def_prop_ro("tooltip", [](const MacroRef& x) { return Text(ResolveMacro(x)->GetToolTip()); }, "Tooltip text.")
        .def_prop_ro("text", [](const MacroRef& x) { return Text(ResolveMacro(x)->GetButtonText()); }, "Button text.")
        .def_prop_ro("file", [](const MacroRef& x) { return Text(ResolveMacro(x)->GetFileName()); }, "Source file.")
        .def_prop_ro("id", [](const MacroRef& x) { return x.id; }, "Its ID in this 3ds Max session's macro directory.")
        .def("run", &RunMacro, "Run it, like macros.run; returns its result.")
        .def("__repr__", &MacroRepr);
    BindIdentity<MacroRef>(macro, [](const MacroRef& x, const MacroRef& y) { return x.id == y.id; },
        [](const MacroRef& x) { return (Py_hash_t)x.id; });

    nb::class_<ActionTableRef> table(m, "ActionTable", "An action table: name, id, actions(pattern).");
    table.def_prop_ro("name", [](const ActionTableRef& t) { return Text(ResolveTable(t.id)->GetName()); },
            "The table's name, as action_tables(pattern) matches it.")
        .def_prop_ro("id", [](const ActionTableRef& t) { return t.id; }, "The table's ActionTableId.")
        .def_prop_ro("context_id", [](const ActionTableRef& t) { return (unsigned long)ResolveTable(t.id)->GetContextId(); },
            "The ActionContextId the table belongs to.")
        .def("actions", [](const ActionTableRef& t, const std::optional<std::string>& pattern) {
            std::vector<ActionRef> out;
            TableActions(ResolveTable(t.id), pattern, out);
            return out;
        }, "pattern"_a = nb::none(), "Its actions, by wildcard menu text, description or category.")
        .def("__len__", [](const ActionTableRef& t) { return ResolveTable(t.id)->Count(); })
        .def("__repr__", [](const ActionTableRef& t) {
            try {
                ActionTable* found = ResolveTable(t.id);
                return "<ActionTable '" + Text(found->GetName()) + "' " + std::to_string(found->Count()) + " actions>";
            } catch (const MaxError&) {
                return "<ActionTable " + std::to_string(t.id) + " removed>";
            }
        });
    BindIdentity<ActionTableRef>(table, [](const ActionTableRef& x, const ActionTableRef& y) { return x.id == y.id; },
        [](const ActionTableRef& t) { return (Py_hash_t)t.id; });

    nb::class_<ActionRef> action(m, "Action", "An action item (menu command, shortcut target): text, description, run().");
    action.def_prop_ro("id", [](const ActionRef& a) { return a.id; }, "The action's command ID within its table.")
        .def_prop_ro("table", [](const ActionRef& a) { ResolveTable(a.table); return ActionTableRef{a.table}; },
            "The ActionTable it belongs to.")
        .def_prop_ro("text", &ActionText, "Menu text.")
        .def_prop_ro("description", [](const ActionRef& a) { return ItemText(ResolveAction(a), &ActionItem::GetDescriptionText); },
            "Description text, as the Customize User Interface dialog shows it.")
        .def_prop_ro("category", [](const ActionRef& a) { return ItemText(ResolveAction(a), &ActionItem::GetCategoryText); },
            "Category text, as the Customize User Interface dialog groups it.")
        .def_prop_ro("macro", [](const ActionRef& a) -> std::optional<MacroRef> {
            MacroEntry* entry = ResolveAction(a)->GetMacroScript();
            if (!entry) return std::nullopt;
            return MacroRef{entry->GetID()};
        }, "The macroscript it runs, or None.")
        .def("run", [](const ActionRef& a) {
            if (!ResolveAction(a)->Execute())
                throw MaxError("action '" + ActionText(a) + "' did not run: it is disabled here or its table is inactive");
        }, "Run it, like actionMan.executeAction.")
        .def("__repr__", [](const ActionRef& a) {
            try {
                return "<Action '" + ActionText(a) + "' " + std::to_string(a.id) + " in '" + Text(ResolveTable(a.table)->GetName()) + "'>";
            } catch (const MaxError&) {
                return "<Action " + std::to_string(a.id) + " removed>";
            }
        });
    BindIdentity<ActionRef>(action, [](const ActionRef& x, const ActionRef& y) { return x.table == y.table && x.id == y.id; },
        [](const ActionRef& a) { return (Py_hash_t)(a.table * 65537 + (unsigned)a.id); });

    m.def("macros", &Macros, "pattern"_a = nb::none(), "category"_a = nb::none(),
          "Macroscripts by wildcard name, tooltip or button text and category: macros('*isolate*').");
    m.def("macro", &FindMacro, "name"_a, "category"_a = nb::none(), "A macroscript by name: macro('SmartIsolate', 'Tools').");
    m.def("action_tables", &ActionTables, "pattern"_a = nb::none(), "Action tables by wildcard name: action_tables('Main*').");
    m.def("actions", &Actions, "pattern"_a = nb::none(), "table"_a = nb::none(),
          "Actions by wildcard menu text, description or category across tables: actions('*Isolate*').");
}

static void BindInspection(nb::module_& m) {
    AddMethod<Node>("inspect", &InspectNode,
        "Report: classes, transform, hierarchy, visibility and render flags, wirecolor, instances (self included), "
        "mesh, bbox, modifiers, material and the plugin files its classes come from. "
        "Keys: {'name', 'handle', 'class', 'superclass', 'base_class', 'pos', 'rot', 'scale', 'parent', "
        "'children', 'layer', 'hidden', 'frozen', 'renderable', 'primary_visibility', 'secondary_visibility', "
        "'cast_shadows', 'receive_shadows', 'wirecolor', 'instances', 'mesh', 'bbox', 'modifiers', 'material', "
        "'plugins'}.");
    AddMethod<Params>("schema", &OwnerSchema, "fields"_a, "query"_a = "", "offset"_a = 0, "limit"_a = 256,
        "Plugin inspector report with live values and the schema and state tokens patch() needs: p.schema('multiplier').");
    AddMethod<Params>("patch", [](const Params& p, nb::dict schema, bool allInstances, nb::kwargs values) {
        if (ReadSchema(schema).owner != ParamOwner(p)) throw BadParam("this schema describes another object; use its own params");
        return PluginPatch(nb::make_tuple(nb::make_tuple(schema, values)), allInstances);
    }, "schema"_a, "all_instances"_a = false, "values"_a,
        "Guarded edit, one undo step, refused if anything inspected changed: p.patch(s, multiplier=2.0). "
        "Enums take their names; all_instances=True allows editing a shared object. properties lists "
        "{'owner', 'name', 'before', 'value'} per edit. Keys: {'status', 'verification', 'transaction', 'properties'}.");
    ExtendParamsDir({"interface", "interfaces", "patch", "schema"});

    m.def("plugin_patch", &PluginPatch, "edits"_a, "all_instances"_a = false,
          "Patch several objects in one guarded undo step: plugin_patch([(s1, {'multiplier': 2}), (s2, {'size': 5})]). "
          "properties lists {'owner', 'name', 'before', 'value'} per edit. "
          "Keys: {'status', 'verification', 'transaction', 'properties'}.");
    m.def("osl_info", &OslInfo, "shader"_a,
          "Compile an OSL shader (a .osl path or a name in 3ds Max's OSL folder) in a temporary map: params as "
          "describe() lines and outputs as (name, type). Keys: {'shader', 'path', 'params', 'outputs'}.");
}

// ── The object protocol ──────────────────────────────────────────
// What every wrapper of a plugin object offers (mx_common.h names the members).
// A class binds what is its own; what it leaves out is bound here once, through
// the object's params: every wrapper has them, and reading them resolves the
// live object or raises NotFound. A node's params are its base object's, so a
// node's class_info is the class class_name names.

static Animatable* ObjectOf(nb::handle self) { return ParamOwner(nb::cast<Params>(self.attr("params"))); }

static ReferenceTarget* TargetOf(nb::handle self) {
    Animatable* object = ObjectOf(self);
    if (!object->IsRefMaker() || !static_cast<ReferenceMaker*>(object)->IsRefTarget())
        throw BadParam("this object holds no references");
    return static_cast<ReferenceTarget*>(object);
}

static void BindObjectProtocol(nb::module_& m) {
    const nb::object notFound = m.attr("NotFound");
    for (const char* name : {"Node", "Modifier", "Material", "Texmap", "Controller", "Effect", "Object"}) {
        const nb::object cls = m.attr(name);
        const auto lacks = [&](const char* member) { return !nb::hasattr(cls, member); };
        if (lacks("handle"))
            AddPropertyTo(cls, "handle",
                [](nb::handle self) { return (unsigned long long)Animatable::GetHandleByAnim(ObjectOf(self)); },
                "The object's animatable handle. In pymxs: rt.getAnimByHandle(x.handle).");
        if (lacks("exists"))
            AddPropertyTo(cls, "exists", [notFound](nb::handle self) {
                try {
                    ObjectOf(self);
                    return true;
                } catch (const NotFound&) {
                    return false;
                } catch (nb::python_error& error) {
                    if (error.matches(notFound)) return false;
                    throw;
                }
            }, "False once the object is gone: deleted, or off the stack or list it was read from.");
        if (lacks("class_info"))
            AddPropertyTo(cls, "class_info", [](nb::handle self) {
                Animatable* object = ObjectOf(self);
                return MakeClassInfo(object->SuperClassID(), object->ClassID());
            }, "The object's class as a ClassInfo (a node's: its base object's): x.class_info.describe() lists what "
               "the class takes, x.class_info.plugin the DLL it comes from.");
        if (lacks("interfaces"))
            AddPropertyTo(cls, "interfaces", [](nb::handle self) { return Mixins(ObjectOf(self)); },
                "Mixin interfaces the object publishes.");
        if (lacks("interface"))
            AddMethodTo(cls, "interface",
                [](nb::handle self, const std::string& name) { return FindMixin(ObjectOf(self), name); }, "name"_a,
                "A mixin interface of the object by name: mod.interface('unwrap2').");
        if (lacks("references"))
            AddMethodTo(cls, "references", [](nb::handle self, int depth) { return ReferenceTree(TargetOf(self), depth); },
                "depth"_a = 3,
                "Reference tree below the object: {'class', 'superclass', 'name', 'references': [{'index', ...}]}; "
                "'references' holds the entries below while depth lasts and 'ref_count' their number once it runs out; "
                "'seen' marks a target listed above, and nodes are listed, not expanded. "
                "Keys: {'class', 'superclass', 'name'?, 'index'?, 'references'?, 'ref_count'?, 'seen'?}.");
    }
}

void BindPlugins(nb::module_& m) {
    BindClasses(m);
    BindInterfaces(m);
    BindTools(m);
    BindInspection(m);
    BindObjectProtocol(m);
}

} // namespace MX_ABI_TAG
} // namespace mx
