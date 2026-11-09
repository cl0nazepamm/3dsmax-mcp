#include "mcp_bridge/plugin_access.h"
#include "mcp_bridge/node_ref.h"
#include "mcp_bridge/core_bridge.h"
#include <hold.h>
#include <units.h>
#include <cmath>
#include <limits>
#include <map>
#include <set>
#include <sstream>
#include <memory>
#include <lslights.h>
#if MAX_SDK_VERSION >= 2024
#include <ColorManagement/IColorPipelineMgr.h>
#endif

// MAXScript exports function-like macros with these JSON method names.
#undef is_array
#undef is_string

namespace PluginAccess {
using namespace HandlerHelpers;
namespace {
constexpr size_t MaxProperties = 4096;
constexpr size_t MaxArray = 256;
[[noreturn]] void Fail(const std::string& code, const std::string& message, const json& detail = json::object()) {
    throw std::runtime_error(StructuredErrorPayload(code, message, detail));
}
std::string Hash(const json& value) {
    // Staleness checksum, not an authentication or authorization token.
    uint64_t hash = 14695981039346656037ULL;
    for (unsigned char c : value.dump()) { hash ^= c; hash *= 1099511628211ULL; }
    std::ostringstream out; out << std::hex << hash; return out.str();
}
std::string Session() {
    return std::to_string(GetCurrentProcessId()) + ":" + std::to_string(McpHost::SceneJournal::Epoch());
}
std::string Lower(std::string text) {
    std::transform(text.begin(), text.end(), text.begin(), [](unsigned char c) { return (char)std::tolower(c); });
    return text;
}
json ClassRef(SClass_ID sid, Class_ID id) {
    return {{"superclass_id", (uint64_t)sid}, {"class_id", {(uint32_t)id.PartA(), (uint32_t)id.PartB()}}};
}
json ClassRef(Animatable* value) { return ClassRef(value->SuperClassID(), value->ClassID()); }
ClassDesc* Descriptor(const json& ref) {
    if (!ref.is_object() || !ref.contains("superclass_id") || !ref.contains("class_id") ||
        !ref["class_id"].is_array() || ref["class_id"].size() != 2) Fail("BAD_CLASS_REF", "Expected superclass_id and two-part class_id.");
    for(const auto& id:ref["class_id"]) if(!id.is_number_integer()||id.get<int64_t>()<0||id.get<int64_t>()>UINT32_MAX) Fail("BAD_CLASS_REF","Class ID parts must be unsigned 32-bit integers.");
    if(!ref["superclass_id"].is_number_integer()||ref["superclass_id"].get<int64_t>()<0||ref["superclass_id"].get<int64_t>()>UINT32_MAX) Fail("BAD_CLASS_REF","Invalid superclass ID.");
    auto* entry = ClassDirectory::GetInstance().FindClassEntry((SClass_ID)ref.at("superclass_id").get<uint64_t>(),
        Class_ID(ref["class_id"][0].get<uint32_t>(), ref["class_id"][1].get<uint32_t>()));
    auto* cd=entry?entry->FullCD():nullptr;
    if (!cd) Fail("PLUGIN_MISSING", "Class descriptor is unavailable.", {{"class_ref", ref}});
    return cd;
}
ClassDesc* FindClass(const json& p) {
    if (p.contains("class_ref") && !p["class_ref"].is_null()) return Descriptor(p["class_ref"]);
    const auto name = Lower(p.value("class_name", ""));
    if (name.empty()) Fail("BAD_PARAM", "class_ref or class_name is required.");
    const auto wideName = Utf8ToWide(name);
    ClassDesc* found = nullptr;
    auto& directory=ClassDirectory::GetInstance();
    for(int s=0;s<directory.Count();++s) {
        auto& list=directory[s];
        if(p.contains("superclass_id")&&list.SuperID()!=p["superclass_id"].get<uint64_t>()) continue;
        // Use SDK iteration: the documented 1-based operator[] contract does
        // not match the live directory on Max 2027.
        for(int i=list.GetFirst(ACC_ALL);i!=-1;i=list.GetNext(ACC_ALL)) {
            auto* cd=list[i].CD(); if(!cd) continue;
            if(!MatchesClassName(wideName, cd->InternalName()) &&
                !MatchesClassName(wideName, list[i].ClassName().data()) &&
                !MatchesClassName(wideName, list[i].NonLocalizedClassName().data())) continue;
            if(found&&(found->ClassID()!=cd->ClassID()||found->SuperClassID()!=cd->SuperClassID()))
                Fail("AMBIGUOUS","Class name is ambiguous; supply class_ref.");
            found=list[i].FullCD();
        }
    }
    if (!found) Fail("PLUGIN_MISSING", "Class not found: " + name);
    return found;
}
json Identity(ClassDesc* cd) {
    json result = ClassRef(cd->SuperClassID(), cd->ClassID());
    result["name"] = ScriptClassName(cd);
    result["label"] = SanitizeScriptName(WideToUtf8(cd->ClassName()));
    result["category"] = WideToUtf8(cd->Category());
    result["plugin"] = nullptr;
    auto& dir = DllDir::GetInstance();
    auto* entry=dir.ClassDir().FindClassEntry(cd->SuperClassID(),cd->ClassID());
    if(entry&&entry->DllNumber()>=0&&entry->DllNumber()<dir.Count()) {
        const int d=entry->DllNumber();
        const auto path = dir[d].GetFullPath();
        WIN32_FILE_ATTRIBUTE_DATA data{};
        json plugin = {{"path", WideToUtf8(path.data())}};
        if (GetFileAttributesExW(path.data(), GetFileExInfoStandard, &data)) {
            plugin["size"] = std::to_string(((uint64_t)data.nFileSizeHigh << 32) | data.nFileSizeLow);
            plugin["modified"] = std::to_string(((uint64_t)data.ftLastWriteTime.dwHighDateTime << 32) | data.ftLastWriteTime.dwLowDateTime);
        }
        result["plugin"] = plugin; return result;
    }
    return result;
}
json EnumChoices(FPInterfaceDesc* desc, EnumID id) {
    if (id == FP_NO_ENUM) return nullptr;
    for (int i = 0; i < desc->enumerations.Count(); ++i) {
        auto* e = desc->enumerations[i]; if (!e || e->ID != id) continue;
        json choices = json::array();
        for (int j = 0; j < e->enumeration.Count() && j < (int)MaxProperties; ++j)
            choices.push_back({{"name", WideToUtf8(e->enumeration[j].name)}, {"value", e->enumeration[j].code}});
        return {{"kind", "enum"}, {"choices", choices}, {"complete", e->enumeration.Count() <= (int)MaxProperties}, {"source", "sdk_published_enum"}};
    }
    return {{"kind", "enum"}, {"choices", nullptr}, {"complete", false}, {"source", "sdk_enum_id_only"}};
}
std::string Resource(ParamBlockDesc2* desc, StringResID id) {
    if (!id) return {};
    const MCHAR* value = desc->GetString(id); return WideToUtf8(value);
}
bool FloatType(int type) { return type == TYPE_FLOAT || type == TYPE_WORLD || type == TYPE_ANGLE || type == TYPE_PCNT_FRAC || type == TYPE_COLOR_CHANNEL; }
bool IntType(int type) { return type == TYPE_INT || type == TYPE_INDEX || type == TYPE_TIMEVALUE || type == TYPE_RADIOBTN_INDEX || type == TYPE_ENUM; }
bool RefType(int type) { return type == TYPE_TEXMAP || type == TYPE_MTL || type == TYPE_INODE || type == TYPE_REFTARG; }
json OwnerRef(Animatable* owner) {
    if (!owner) return nullptr;
    return {{"session", Session()}, {"handle", std::to_string((uint64_t)Animatable::GetHandleByAnim(owner))}, {"class_ref", ClassRef(owner)}};
}
Animatable* ResolveLink(const json& binding);
Animatable* ResolveOwner(const json& ref) {
    static thread_local unsigned depth=0;
    if(depth>=24) Fail("BAD_OWNER_REF","Reference binding nesting exceeds 24 levels.");
    struct Depth { unsigned& value; Depth(unsigned& d):value(d) { ++value; } ~Depth() { --value; } } guard(depth);
    if (!ref.is_object()) Fail("BAD_OWNER_REF", "owner_ref must be an object.");
    if (ref.contains("session")) {
        if (ref["session"] != Session()) Fail("STALE_OWNER", "Owner belongs to a different scene/session.");
        auto* owner = Animatable::GetAnimByHandle((AnimHandle)NodeRefs::ParseHandle(ref.at("handle")));
        if (!owner || ClassRef(owner) != ref.at("class_ref")) Fail("STALE_OWNER", "Owner no longer resolves to the inspected class.");
        if (ref.contains("binding") && ResolveLink(ref["binding"]) != owner) Fail("STALE_BINDING", "The inspected reference link changed.");
        if (ref.contains("root_binding") && ResolveOwner(ref["root_binding"]) != owner) Fail("STALE_BINDING", "The node/renderer owner binding changed.");
        return owner;
    }
    if (ref.value("root", "") == "renderer") { auto* renderer=GetCOREInterface()->GetRenderer(RS_Production); if(!renderer) Fail("NOT_FOUND","No production renderer."); return renderer; }
    if (ref.value("root", "") == "environment") {
        auto* map = GetCOREInterface()->GetEnvironmentMap(); if (!map) Fail("NOT_FOUND", "No scene environment map is assigned."); return map;
    }
    auto* node = NodeRefs::Resolve(ref.at("node"));
    const auto scope = ref.value("scope", "base_object");
    if (scope == "node") return node;
    if (scope == "material") { if (!node->GetMtl()) Fail("NOT_FOUND", "No material is assigned."); return node->GetMtl(); }
    if (scope == "modifier") {
        int index=ref.at("modifier_index").get<int>(); if(index<1) Fail("BAD_OWNER_REF","modifier_index is 1-based.");
        Object* current=node->GetObjectRef();
        while(current&&current->SuperClassID()==GEN_DERIVOB_CLASS_ID) {
            auto* derived=static_cast<IDerivedObject*>(current);
            if(index<=derived->NumModifiers()) return derived->GetModifier(index-1);
            index-=derived->NumModifiers(); current=derived->GetObjRef();
        }
        Fail("NOT_FOUND","Modifier index not found.");
    }
    if (scope != "base_object") Fail("BAD_OWNER_REF", "Use a returned owner_ref for modifiers/maps; node scope supports node, base_object or material.");
    auto* obj = node->GetObjectRef(); if (!obj) Fail("NOT_FOUND", "No base object.");
    return obj->FindBaseObject();
}
struct Binding { IParamBlock2* block; ParamID id; const ParamDef* def; Animatable* owner; };
void ValidatePropertyRef(const json& ref) {
    if(!ref.is_object() || ref.value("route","pb2")!="pb2") Fail("BAD_PROPERTY_REF","Expected a PB2 property reference.");
    for(const auto* key:{"block_id","param_id"}) {
        if(!ref.contains(key)||!ref[key].is_number_integer()||ref[key].get<int64_t>()<0||ref[key].get<int64_t>()>SHRT_MAX)
            Fail("BAD_PROPERTY_REF","PB2 block/parameter IDs must be nonnegative 16-bit integers.");
    }
}
Binding ResolveProperty(Animatable* owner, const json& ref) {
    ValidatePropertyRef(ref);
    if (!ref.is_object() || ref.value("route", "pb2") != "pb2") Fail("UNSUPPORTED_PROPERTY", "This edit route supports typed PB2 properties only.");
    const int blockID = ref.at("block_id").get<int>(); const int id = ref.at("param_id").get<int>();
    for (int i = 0; i < owner->NumParamBlocks(); ++i) {
        auto* pb = owner->GetParamBlock(i); if (!pb || pb->ID() != blockID) continue;
        auto* desc = pb->GetDesc();
        if (desc && desc->IDtoIndex((ParamID)id) >= 0) return {pb, (ParamID)id, &desc->GetParamDef((ParamID)id), owner};
    }
    Fail("STALE_PROPERTY", "Parameter ID is unavailable on this owner.", {{"property_ref", ref}});
}
ReferenceTarget* ReadReference(const Binding& b, TimeValue t, int index);
Animatable* ResolveLink(const json& link) {
    auto* parent=ResolveOwner(link.at("owner_ref")); auto b=ResolveProperty(parent,link.at("property_ref"));
    if(!RefType(BaseType(b.def->type))) Fail("STALE_BINDING","Property is no longer a reference.");
    return ReadReference(b,GetCOREInterface()->GetTime(),link.value("index",0));
}
ReferenceTarget* ReadReference(const Binding& b, TimeValue t, int index) {
    Interval valid = FOREVER;
    switch (BaseType(b.def->type)) {
    case TYPE_TEXMAP: { Texmap* x = nullptr; if (!b.block->GetValue(b.id,t,x,valid,index)) Fail("READ_FAILED","Texture read failed."); return x; }
    case TYPE_MTL: { Mtl* x = nullptr; if (!b.block->GetValue(b.id,t,x,valid,index)) Fail("READ_FAILED","Material read failed."); return x; }
    case TYPE_INODE: { INode* x = nullptr; if (!b.block->GetValue(b.id,t,x,valid,index)) Fail("READ_FAILED","Node read failed."); return x; }
    default: { ReferenceTarget* x = nullptr; if (!b.block->GetValue(b.id,t,x,valid,index)) Fail("READ_FAILED","Reference read failed."); return x; }
    }
}
json ReadElement(const Binding& b, TimeValue t, int index) {
    const int type = BaseType(b.def->type); Interval valid = FOREVER; BOOL ok = FALSE; json result;
    if (FloatType(type)) { float x=0; ok=b.block->GetValue(b.id,t,x,valid,index); result=x; }
    else if (IntType(type) || type == TYPE_BOOL || type == TYPE_bool) { int x=0; ok=b.block->GetValue(b.id,t,x,valid,index); result=(type==TYPE_BOOL || type==TYPE_bool)?json(x!=0):json(x); }
    else if (type == TYPE_RGBA || type == TYPE_POINT3) { Point3 x; ok=b.block->GetValue(b.id,t,x,valid,index); result={x.x,x.y,x.z}; }
    else if (type == TYPE_POINT2) { Point2 x; ok=b.block->GetValue(b.id,t,x,valid,index); result={x.x,x.y}; }
    else if (type == TYPE_POINT4 || type == TYPE_FRGBA) { Point4 x; ok=b.block->GetValue(b.id,t,x,valid,index); result={x.x,x.y,x.z,x.w}; }
    else if (type == TYPE_STRING || type == TYPE_FILENAME) { const MCHAR* x=nullptr; ok=b.block->GetValue(b.id,t,x,valid,index); result=x?json(WideToUtf8(x)):json(nullptr); }
    else if (RefType(type)) {
        auto result=OwnerRef(ReadReference(b,t,index));
        if(!result.is_null()) result["binding"]={{"owner_ref",OwnerRef(b.owner)},{"property_ref",{{"route","pb2"},{"block_id",b.block->ID()},{"param_id",b.id}}},{"index",index}};
        return result;
    }
    else Fail("UNSUPPORTED_TYPE", "No typed value reader for " + TypeName(b.def->type));
    if (!ok) Fail("READ_FAILED", "Plugin did not return a value.");
    return result;
}
json Read(const Binding& b, TimeValue t) {
    if (!(b.def->type & TYPE_TAB)) return ReadElement(b,t,0);
    const int count = b.block->Count(b.id);
    if (count < 0 || count > (int)MaxArray) Fail("ARRAY_LIMIT", "Array exceeds 256 elements; inspect individual elements.");
    json values=json::array(); for (int i=0;i<count;++i) values.push_back(ReadElement(b,t,i)); return values;
}
json Schema(ClassDesc* cd, Animatable* owner=nullptr) {
    json out={{"identity",Identity(cd)}, {"properties",json::array()}, {"interfaces",json::array()}, {"schema_version",2}};
    auto* cd2=dynamic_cast<ClassDesc2*>(cd); size_t count=0;
    const int blocks=owner?owner->NumParamBlocks():(cd2?cd2->NumParamBlockDescs():0);
    for (int i=0;i<blocks;++i) {
        auto* pb=owner?owner->GetParamBlock(i):nullptr;
        auto* desc=owner?(pb?pb->GetDesc():nullptr):cd2->GetParamBlockDesc(i); if(!desc) continue;
        for(int j=0;j<desc->count && count<MaxProperties;++j,++count) out["properties"].push_back(DescribeParameter(desc,desc->GetParamDef(desc->IndextoID(j))));
    }
    if(cd2) for(int i=0;i<cd2->NumInterfaces() && i<128;++i) {
        auto* iface=cd2->GetInterfaceAt(i); if(iface) out["interfaces"].push_back(DescribeInterface(iface));
    }
    out["coverage"]={{"param_blocks",blocks},{"property_limit_reached",count>=MaxProperties},{"scope","PB2 descriptors and published interface metadata"},{"unavailable",{"unpublished methods","custom UI enums","PB1 semantic metadata"}}};
    out["schema_token"]=Hash(out); return out;
}
bool Matches(const json& property,const json& request) {
    const auto query=Lower(request.value("query",""));
    if(!query.empty() && Lower(property.value("name","")+" "+property.value("label","")+" "+property.value("description","")).find(query)==std::string::npos) return false;
    if(request.contains("fields") && !request["fields"].empty()) {
        bool found=false; for(const auto& field:request["fields"]) if(field==property["property_ref"] || field==property["name"]) found=true;
        if(!found) return false;
    }
    return true;
}
json PropertyValues(Animatable* owner,const json& schema,const json& request) {
    json values=json::array(); const auto t=GetCOREInterface()->GetTime();
    for(auto property:schema["properties"]) {
        if(!Matches(property,request)) continue;
        const auto name=Lower(property.value("name",""));
        if(name=="adtexturelock" || name=="notused" || name=="thelist" || name=="geometryorientationlookatnode" || name=="target_distance") {
            property["value_status"]="skipped_known_unsafe";
        } else try {
            auto b=ResolveProperty(owner,property["property_ref"]);
            property["value"]=Read(b,t); property["value_status"]="read";
            auto* controller=b.block->GetControllerByID(b.id);
            property["controller_ref"]=OwnerRef(controller);
            property["animated"]=controller?controller->IsAnimated()!=FALSE:false;
        } catch(const std::exception& e) { property["value_status"]="unavailable"; property["value_error"]=e.what(); }
        values.push_back(std::move(property));
    }
    return values;
}
json State(Animatable* owner,const json& schema,const json& fields) {
    return {{"owner_ref",OwnerRef(owner)},{"time",GetCOREInterface()->GetTime()},{"properties",fields.empty()?json::array():PropertyValues(owner,schema,{{"fields",fields}})}};
}
bool Equal(const json& a,const json& b) {
    if(a.is_object()&&b.is_object()&&a.contains("session")&&b.contains("session")&&a.contains("handle")&&b.contains("handle"))
        return a.at("session")==b.at("session")&&a.at("handle")==b.at("handle")&&a.at("class_ref")==b.at("class_ref");
    if(a.is_number()&&b.is_number()) { const double x=a.get<double>(),y=b.get<double>(); return std::isfinite(x)&&std::isfinite(y)&&std::abs(x-y)<=1e-5*std::max(1.0,std::max(std::abs(x),std::abs(y))); }
    if(a.is_array()&&b.is_array()) { if(a.size()!=b.size()) return false; for(size_t i=0;i<a.size();++i) if(!Equal(a[i],b[i])) return false; return true; }
    return a==b;
}
}

int BaseType(int type) { return type & ~(TYPE_TAB|TYPE_BY_REF|TYPE_BY_VAL|TYPE_BY_PTR); }
std::string TypeName(int type) {
    const int base=BaseType(type); std::string name;
    switch(base) {
    case TYPE_FLOAT:name="float";break; case TYPE_INT:name="int";break; case TYPE_BOOL:case TYPE_bool:name="bool";break;
    case TYPE_RGBA:name="color";break; case TYPE_FRGBA:name="color4";break; case TYPE_POINT2:name="point2";break; case TYPE_POINT3:name="point3";break; case TYPE_POINT4:name="point4";break;
    case TYPE_WORLD:name="worldUnits";break; case TYPE_ANGLE:name="angle";break; case TYPE_PCNT_FRAC:name="percent";break; case TYPE_COLOR_CHANNEL:name="colorChannel";break;
    case TYPE_TEXMAP:name="texturemap";break; case TYPE_MTL:name="material";break; case TYPE_INODE:name="node";break; case TYPE_REFTARG:name="refTarget";break;
    case TYPE_STRING:name="string";break; case TYPE_FILENAME:name="filename";break; case TYPE_ENUM:name="enum";break; case TYPE_INDEX:name="index";break; case TYPE_RADIOBTN_INDEX:name="radioIndex";break;
    case TYPE_TIMEVALUE:name="time";break; case TYPE_VALUE:name="maxValue";break; case TYPE_INT64:name="int64";break; case TYPE_DOUBLE:name="double";break; case TYPE_DWORD:name="dword";break;
    default:name="type_"+std::to_string(base);
    }
    if(type&TYPE_TAB) name+="[]"; return name;
}
json DescribeParameter(ParamBlockDesc2* block,const ParamDef& pd) {
    const int type=BaseType(pd.type);
    json out={{"property_ref",{{"route","pb2"},{"block_id",block->ID},{"param_id",pd.ID}}},
        {"name",WideToUtf8(pd.int_name)},{"label",Resource(block,pd.local_name)},
        {"description",Resource(block,pd.description)},{"type",TypeName(pd.type)},{"raw_type",(int)pd.type},
        {"animatable",(pd.flags&P_ANIMATABLE)!=0},{"read_only",(pd.flags&P_READ_ONLY)!=0},
        {"transient",(pd.flags&P_TRANSIENT)!=0},{"has_accessor",pd.accessor!=nullptr},{"has_validator",pd.validator!=nullptr},
        {"array",(pd.type&TYPE_TAB)!=0},{"index_base",0},{"source","sdk_pb2"}};
    out["unit"]=type==TYPE_WORLD?json("scene"):type==TYPE_ANGLE?json("radians"):type==TYPE_PCNT_FRAC?json("fraction"):type==TYPE_TIMEVALUE?json("ticks"):json(nullptr);
    out["domain"]={{"kind",type==TYPE_BOOL||type==TYPE_bool?"boolean":"unknown"},{"choices",nullptr},{"complete",type==TYPE_BOOL||type==TYPE_bool}};
    if(pd.flags&P_HAS_TOOLTIP) out["tooltip"]=Resource(block,pd.toolTip);
    if(pd.flags&P_HAS_DEFAULT) {
        const auto key=(pd.type&TYPE_TAB)?"element_default":"default";
        if(FloatType(type)) out[key]=pd.def.f;
        else if(IntType(type)) out[key]=pd.def.i;
        else if(type==TYPE_BOOL||type==TYPE_bool) out[key]=pd.def.i!=0;
        else if((type==TYPE_POINT3||type==TYPE_RGBA)&&pd.def.p) out[key]={pd.def.p->x,pd.def.p->y,pd.def.p->z};
    }
    if(pd.flags&P_HAS_RANGE) {
        if(FloatType(type)) out["range"]={pd.range_low.f,pd.range_high.f};
        else if(IntType(type)) out["range"]={pd.range_low.i,pd.range_high.i};
    }
    if(pd.flags&P_HAS_CLASS_ID) out["reference_class_id"]={pd.class_ID.PartA(),pd.class_ID.PartB()};
    if(pd.flags&P_HAS_SCLASS_ID) out["reference_superclass_id"]=(uint64_t)pd.sclass_ID;
    return out;
}
json DescribeInterface(FPInterface* value) {
    auto* desc=value->GetDesc(); if(!desc) return {{"coverage","descriptor_unavailable"}};
    json out={{"name",WideToUtf8(desc->internal_name.data())},{"id",{desc->GetID().PartA(),desc->GetID().PartB()}},{"functions",json::array()},{"properties",json::array()}};
    for(int i=0;i<desc->functions.Count() && i<(int)MaxProperties;++i) {
        auto* f=desc->functions[i]; if(!f) continue;
        json fn={{"name",WideToUtf8(f->internal_name.data())},{"id",f->ID},{"returnType",TypeName(f->result_type)},{"returnDomain",EnumChoices(desc,f->enumID)},{"params",json::array()}};
        for(int j=0;j<f->params.Count();++j) { auto* p=f->params[j]; if(p) fn["params"].push_back({{"name",WideToUtf8(p->internal_name.data())},{"type",TypeName(p->type)},{"raw_type",(int)p->type},{"domain",EnumChoices(desc,p->enumID)}}); }
        out["functions"].push_back(fn);
    }
    for(int i=0;i<desc->props.Count() && i<(int)MaxProperties;++i) {
        auto* p=desc->props[i]; if(p) out["properties"].push_back({{"name",WideToUtf8(p->internal_name.data())},{"type",TypeName(p->prop_type)},
            {"raw_type",(int)p->prop_type},{"getter_id",p->getter_ID},{"setter_id",p->setter_ID},{"readOnly",p->setter_ID==FPS_NO_SUCH_FUNCTION},
            {"domain",EnumChoices(desc,p->enumID)},{"value_status","metadata_only"}});
    }
    return out;
}
json Inspect(const json& p) {
    Animatable* owner=nullptr;
    if(p.contains("owner_ref")&&!p["owner_ref"].is_null()) owner=ResolveOwner(p["owner_ref"]);
    auto* cd=owner?Descriptor(ClassRef(owner)):FindClass(p);
    auto schema=Schema(cd,owner); const int limit=p.value("limit",25),offset=p.value("offset",0);
    if(limit<1||limit>256||offset<0) Fail("BAD_PARAM","limit must be 1..256 and offset nonnegative.");
    auto all=schema["properties"];
    json matched=json::array(); for(const auto& item:all) if(Matches(item,p)) matched.push_back(item);
    json page=json::array(); for(size_t i=(size_t)offset;i<matched.size()&&page.size()<(size_t)limit;++i) page.push_back(matched[i]);
    if(owner) { json selected=schema; selected["properties"]=page; page=PropertyValues(owner,selected,p); }
    schema["properties"]=page; schema["total"]=matched.size(); schema["offset"]=offset;
    schema["next_offset"]=offset+(int)page.size()<(int)matched.size()?json(offset+page.size()):json(nullptr);
    if(owner) {
        json fields=json::array(); for(const auto& property:page) fields.push_back(property["property_ref"]);
        // Hash the values actually returned, without calling plugin getters twice.
        json state={{"owner_ref",OwnerRef(owner)},{"time",GetCOREInterface()->GetTime()},{"properties",page}};
        auto ref=OwnerRef(owner);
        if(p["owner_ref"].contains("session")) ref=p["owner_ref"];
        else ref["root_binding"]=p["owner_ref"];
        schema["owner_ref"]=ref; schema["state_token"]={{"fields",fields},{"hash",Hash(state)}};
        schema["time"]=GetCOREInterface()->GetTime();
        schema["references"]=json::array();
        for(const auto& property:page) if(property.value("value_status","")=="read" && property["value"].is_object() && property["value"].contains("handle"))
            schema["references"].push_back({{"property_ref",property["property_ref"]},{"owner_ref",property["value"]}});
    }
    return schema;
}
json Context() {
    auto* ip=GetCOREInterface(); json renderers=json::array();
    auto* list=ClassDirectory::GetInstance().GetClassList(RENDERER_CLASS_ID);
    if(list) for(int i=list->GetFirst(ACC_ALL);i!=-1;i=list->GetNext(ACC_ALL)) {
        auto* cd=(*list)[i].CD(); if(cd&&(*list)[i].IsPublic()) renderers.push_back(Identity(cd));
    }
    auto* renderer=ip->GetRenderer(RS_Production);
    json result={{"session",Session()},{"max_version",MAX_SDK_VERSION},{"meters_per_unit",GetSystemUnitScale(UNITS_METERS)},
        {"renderer",renderer?Identity(Descriptor(ClassRef(renderer))):json(nullptr)}, {"renderers",renderers},
        {"environment",OwnerRef(ip->GetEnvironmentMap())},{"environment_enabled",ip->GetUseEnvironmentMap()!=FALSE}};
    result["color_management"]={{"status","legacy_or_unavailable"}};
#if MAX_SDK_VERSION >= 2024
    if(auto* manager=MaxSDK::ColorManagement::IColorPipelineMgr::GetInstance()) if(auto* settings=manager->Settings()) {
        result["color_management"]={{"mode",(int)settings->Mode()},{"status",(int)settings->GetStatus()},
            {"rendering_space",WideToUtf8(settings->GetCurrentRenderingColorSpace().data())},{"config",WideToUtf8(settings->GetOCIOConfigFilePath().data())}};
    }
#endif
    result["context_token"]=Hash(result); return result;
}

namespace {
bool UsesPhotometricNodeMaker(ClassDesc* cd) {
    if(cd->SuperClassID()!=LIGHT_CLASS_ID) return false;
    const auto id=cd->ClassID();
    // These free-light classes require Max's light maker initialization for
    // Nitrous display. Exact IDs avoid localized names or constructor scripts.
    return id==LS_POINT_LIGHT_ID||id==LS_AREA_LIGHT_ID||id==LS_DISC_LIGHT_ID||
        id==LS_SPHERE_LIGHT_ID||id==LS_CYLINDER_LIGHT_ID;
}
INode* CreatePhotometricNode(ClassDesc* cd) {
    RequireMaxScriptThread();
    init_thread_locals();
    one_value_local(created);
    auto classID=cd->ClassID();
    auto* constructor=MAXClass::lookup_class(&classID,LIGHT_CLASS_ID);
    if(!constructor) Fail("CREATE_FAILED","Photometric light maker is unavailable.");
    vl.created=constructor->apply(nullptr,0);
    if(!vl.created) Fail("CREATE_FAILED","Photometric light maker returned no node.");
    return vl.created->to_node();
}
// Pin (a ReferenceMaker holding one reference) and the environment undo
// record live in the shell (undo_records.cpp): Max keeps them as dependents
// and on the undo stack after the request.
using Pin = McpHost::UndoRecords::Pin;
void ValidateElement(const ParamDef& pd,const json& value) {
    const int type=BaseType(pd.type);
    if(FloatType(type)) {
        if(!value.is_number()||!std::isfinite(value.get<double>())||std::abs(value.get<double>())>std::numeric_limits<float>::max()) Fail("BAD_VALUE","Expected a finite float.");
    } else if(IntType(type)) {
        if(!value.is_number_integer()||value.get<int64_t>()<INT_MIN||value.get<int64_t>()>INT_MAX) Fail("BAD_VALUE","Expected a 32-bit integer.");
    } else if(type==TYPE_BOOL||type==TYPE_bool) { if(!value.is_boolean()) Fail("BAD_VALUE","Expected a boolean, not an integer enum."); }
    else if(type==TYPE_STRING||type==TYPE_FILENAME) { if(!value.is_string()||value.get_ref<const std::string&>().find('\0')!=std::string::npos) Fail("BAD_VALUE","Expected a string without NUL bytes."); }
    else if(type==TYPE_POINT2||type==TYPE_POINT3||type==TYPE_POINT4||type==TYPE_RGBA||type==TYPE_FRGBA) {
        const size_t size=type==TYPE_POINT2?2:(type==TYPE_POINT4||type==TYPE_FRGBA?4:3);
        if(!value.is_array()||value.size()!=size) Fail("BAD_VALUE","Vector/color has the wrong number of components.");
        for(const auto& x:value) if(!x.is_number()||!std::isfinite(x.get<double>())||std::abs(x.get<double>())>std::numeric_limits<float>::max()) Fail("BAD_VALUE","Vector/color components must be finite floats.");
    } else if(RefType(type)) { if(!value.is_null()&&!value.is_object()) Fail("BAD_VALUE","Expected an owner_ref or created reference."); }
    else Fail("UNSUPPORTED_TYPE","No typed setter for "+TypeName(pd.type));
    if(pd.flags&P_HAS_RANGE) {
        if(FloatType(type) && (value.get<double>()<pd.range_low.f || value.get<double>()>pd.range_high.f)) Fail("OUT_OF_RANGE","Value lies outside the declared parameter range.");
        if(IntType(type) && (value.get<int64_t>()<pd.range_low.i || value.get<int64_t>()>pd.range_high.i)) Fail("OUT_OF_RANGE","Value lies outside the declared parameter range.");
    }
}
void Validate(const ParamDef& pd,const json& value) {
    if(pd.flags&P_READ_ONLY) Fail("READ_ONLY","The plugin marks this parameter read-only.");
    if(pd.type&TYPE_TAB) {
        if(!value.is_array()||value.size()>MaxArray) Fail("BAD_VALUE","Expected a bounded array.");
        for(const auto& item:value) ValidateElement(pd,item);
    } else ValidateElement(pd,value);
}
using Created=std::map<std::string,Animatable*>;
Animatable* ResolvePlanOwner(const json& ref,const Created& created) {
    if(ref.is_object()&&ref.contains("created")) {
        auto it=created.find(ref.at("created").get<std::string>()); if(it==created.end()) Fail("BAD_PLAN","Unknown created resource."); return it->second;
    }
    return ResolveOwner(ref);
}
void SetElement(const Binding& b,const json& value,TimeValue t,int index,const Created& created) {
    const int type=BaseType(b.def->type); BOOL ok=FALSE;
    if(FloatType(type)) ok=b.block->SetValue(b.id,t,value.get<float>(),index);
    else if(IntType(type)) ok=b.block->SetValue(b.id,t,value.get<int>(),index);
    else if(type==TYPE_BOOL||type==TYPE_bool) ok=b.block->SetValue(b.id,t,(int)value.get<bool>(),index);
    else if(type==TYPE_STRING||type==TYPE_FILENAME) { auto text=Utf8ToWide(value.get<std::string>()); ok=b.block->SetValue(b.id,t,text.c_str(),index); }
    else if(type==TYPE_POINT2) ok=b.block->SetValue(b.id,t,Point2(value[0].get<float>(),value[1].get<float>()),index);
    else if(type==TYPE_POINT3||type==TYPE_RGBA) ok=b.block->SetValue(b.id,t,Point3(value[0].get<float>(),value[1].get<float>(),value[2].get<float>()),index);
    else if(type==TYPE_POINT4||type==TYPE_FRGBA) ok=b.block->SetValue(b.id,t,Point4(value[0].get<float>(),value[1].get<float>(),value[2].get<float>(),value[3].get<float>()),index);
    else if(RefType(type)) {
        auto* target=value.is_null()?nullptr:dynamic_cast<ReferenceTarget*>(ResolvePlanOwner(value,created));
        if(!value.is_null()&&!target) Fail("BAD_REFERENCE","Expected a ReferenceTarget.");
        if(target && (b.def->flags&P_HAS_CLASS_ID) && target->ClassID()!=b.def->class_ID) Fail("BAD_REFERENCE","Reference class does not match the validator.");
        if(target && (b.def->flags&P_HAS_SCLASS_ID) && target->SuperClassID()!=b.def->sclass_ID) Fail("BAD_REFERENCE","Reference superclass does not match the validator.");
        if(type==TYPE_TEXMAP) { if(target&&target->SuperClassID()!=TEXMAP_CLASS_ID) Fail("BAD_REFERENCE","Expected a texture map."); ok=b.block->SetValue(b.id,t,static_cast<Texmap*>(target),index); }
        else if(type==TYPE_MTL) { if(target&&target->SuperClassID()!=MATERIAL_CLASS_ID) Fail("BAD_REFERENCE","Expected a material."); ok=b.block->SetValue(b.id,t,static_cast<Mtl*>(target),index); }
        else if(type==TYPE_INODE) { if(target&&target->SuperClassID()!=BASENODE_CLASS_ID) Fail("BAD_REFERENCE","Expected a scene node."); ok=b.block->SetValue(b.id,t,static_cast<INode*>(target),index); }
        else ok=b.block->SetValue(b.id,t,target,index);
    }
    if(!ok) Fail("SET_FAILED","Plugin rejected the typed parameter assignment.");
}
void Set(const Binding& b,const json& value,TimeValue t,const Created& created) {
    Validate(*b.def,value);
    if(b.def->type&TYPE_TAB) {
        if((int)value.size()!=b.block->Count(b.id)) Fail("ARRAY_SIZE","Array resizing is not implicit; preserve the existing element count.");
        for(int i=0;i<(int)value.size();++i) SetElement(b,value[i],t,i,created);
    } else SetElement(b,value,t,0,created);
}
json ExpectedValue(const json& value,const Created& created) {
    if(value.is_object()&&(value.contains("created")||value.contains("session"))) return OwnerRef(ResolvePlanOwner(value,created));
    if(value.is_array()) { json out=json::array(); for(const auto& x:value) out.push_back(ExpectedValue(x,created)); return out; }
    return value;
}
bool Shared(Animatable* owner, json* evidence=nullptr, std::set<Animatable*>* visited=nullptr) {
    // A scene root is a terminal consumer, like a node. References to the
    // whole scene do not imply additional uses of each map inside that scene.
    if(owner==GetCOREInterface()->GetScenePointer()) return false;
    std::set<Animatable*> local; if(!visited) visited=&local;
    if(!visited->insert(owner).second) return false;
    auto* rt=dynamic_cast<ReferenceTarget*>(owner); if(!rt) return false;
    // RenderEnvironment is a real ReferenceTarget consumer. Count that edge
    // once; adding a synthetic environment use would count the same slot twice.
    DependentIterator it(rt); int count=0;
    json consumers=json::array();
    while(auto* dep=it.Next()) {
        // These keep targets alive for scripting/undo; they are not scene
        // consumers. Both report IsRealDependency=true in the live SDK.
        if(McpHost::UndoRecords::IsPin(dep)||dynamic_cast<MAXWrapper*>(dep)||
            dynamic_cast<RestoreObj*>(dep)||!dep->IsRealDependency(rt)||
            !dynamic_cast<ReferenceTarget*>(dep)||
            dep==GetCOREInterface()->GetSceneMtls()||
            (dep->SuperClassID()==REF_MAKER_CLASS_ID&&dep->ClassID()==Class_ID(MEDIT_CLASS_ID,0))) continue;
        // Scene inventory and Material Editor slots expose existing maps;
        // neither creates another shading/environment use.
        MSTR consumerName; dep->GetClassName(consumerName);
        consumers.push_back({{"owner_ref",OwnerRef(dep)},{"class_name",WideToUtf8(consumerName.data())}});
        if(++count>1) {
            if(evidence) *evidence={{"shared_owner",OwnerRef(owner)},{"consumers",consumers}};
            return true;
        }
        // A map can have one parent which is itself instanced.
        if(dep->SuperClassID()!=BASENODE_CLASS_ID&&Shared(dep,evidence,visited)) return true;
    }
    return false;
}
const ParamDef& ClassParameter(ClassDesc* cd,const json& ref) {
    ValidatePropertyRef(ref);
    auto* cd2=dynamic_cast<ClassDesc2*>(cd); if(!cd2) Fail("UNSUPPORTED_PROPERTY","No PB2 class descriptor.");
    if(ref.value("route","pb2")!="pb2") Fail("UNSUPPORTED_PROPERTY","Only PB2 writes are supported.");
    for(int i=0;i<cd2->NumParamBlockDescs();++i) {
        auto* desc=cd2->GetParamBlockDesc(i); if(desc&&desc->ID==ref.at("block_id").get<int>()&&desc->IDtoIndex((ParamID)ref.at("param_id").get<int>())>=0)
            return desc->GetParamDef((ParamID)ref.at("param_id").get<int>());
    }
    Fail("STALE_PROPERTY","Class does not declare the requested parameter.");
}
}

json Apply(const json& p) {
    if(p.value("version",1)!=1) Fail("BAD_PLAN","Unsupported edit plan version.");
    if(theHold.Holding()) Fail("USER_BUSY","3ds Max has an open undo operation; no changes were made.");
    auto* ip=GetCOREInterface(); const auto t=ip->GetTime();
    if(GetCOREInterface8()->IsRenderActive()) Fail("RENDER_BUSY","A production render is active; no changes were made.");
    if(p.contains("expected_context")&&!p["expected_context"].is_null()&&p["expected_context"]!=Context()["context_token"]) Fail("STALE_CONTEXT","Renderer, units or environment binding changed.");
    const auto creates=p.value("creates",json::array()),edits=p.value("edits",json::array());
    if(!creates.is_array()||!edits.is_array()||creates.size()>128||edits.size()>512||creates.empty()&&edits.empty()&&!p.contains("environment")) Fail("BAD_PLAN","Expected a bounded, nonempty edit plan.");
    const auto guards=p.value("guards",json::array());
    if(!guards.is_array()||guards.size()>128) Fail("BAD_PLAN","Expected at most 128 dependency guards.");
    for(const auto& guard:guards) {
        auto* owner=ResolveOwner(guard.at("owner_ref")); auto schema=Schema(Descriptor(ClassRef(owner)),owner);
        if(guard.at("expected_schema")!=schema["schema_token"]) Fail("STALE_SCHEMA","An inspected dependency schema changed.");
        auto token=guard.at("expected_state");
        if(token.at("hash")!=Hash(State(owner,schema,token.at("fields")))) Fail("STALE_STATE","An inspected dependency changed before the transaction.");
    }
    std::map<std::string,ClassDesc*> classes; std::set<std::string> names;
    for(const auto& item:creates) {
        const auto id=item.at("id").get<std::string>(); if(id.empty()||classes.count(id)) Fail("BAD_PLAN","Created IDs must be nonempty and unique.");
        auto* cd=Descriptor(item.at("class_ref")); const auto sid=cd->SuperClassID();
        if(sid!=LIGHT_CLASS_ID&&sid!=TEXMAP_CLASS_ID&&sid!=MATERIAL_CLASS_ID) Fail("BAD_CLASS_REF","Construction is limited to light, map and material classes.");
        if(item.contains("expected_schema")&&item["expected_schema"]!=Schema(cd)["schema_token"]) Fail("STALE_SCHEMA","Creation schema changed.");
        const auto name=item.value("name",""); if(sid==LIGHT_CLASS_ID&&!name.empty()&&(!names.insert(name).second||!CollectNodesByName(name).empty())) Fail("NAME_COLLISION","Light name already exists: "+name);
        if(item.contains("matrix")) { if(sid!=LIGHT_CLASS_ID||!item["matrix"].is_array()||item["matrix"].size()!=4) Fail("BAD_PLAN","Only lights accept a four-row world matrix.");
            for(const auto& row:item["matrix"]) { if(!row.is_array()||row.size()!=3) Fail("BAD_PLAN","Invalid matrix row."); for(const auto& x:row) if(!x.is_number()||!std::isfinite(x.get<double>())) Fail("BAD_PLAN","Matrix must be finite."); } }
        classes[id]=cd;
    }
    Created created; std::map<std::string,INode*> nodes; std::vector<AnimHandle> nodeHandles;
    struct Planned { Animatable* existing; json edit; json before; };
    std::vector<Planned> planned; std::set<std::string> writes;
    for(const auto& edit:edits) {
        const auto target=edit.at("owner_ref"); const auto prop=edit.at("property_ref");
        if(!writes.insert(target.dump()+prop.dump()).second) Fail("BAD_PLAN","A property can be assigned only once per batch.");
        if(target.contains("created")) {
            auto it=classes.find(target["created"].get<std::string>()); if(it==classes.end()) Fail("BAD_PLAN","Unknown created ID.");
            Validate(ClassParameter(it->second,prop),edit.at("value")); planned.push_back({nullptr,edit,nullptr});
        } else {
            auto* owner=ResolveOwner(target); auto schema=Schema(Descriptor(ClassRef(owner)),owner);
            if(edit.at("expected_schema")!=schema["schema_token"]) Fail("STALE_SCHEMA","Property schema changed.");
            auto token=edit.at("expected_state"); auto fields=token.at("fields");
            if(std::find(fields.begin(),fields.end(),prop)==fields.end()) Fail("STALE_PROPERTY","Inspect this property before editing it.");
            if(token.at("hash")!=Hash(State(owner,schema,fields))) Fail("STALE_STATE","An inspected value, controller or time changed.");
            auto b=ResolveProperty(owner,prop); Validate(*b.def,edit.at("value"));
            for(int i=0;i<((b.def->type&TYPE_TAB)?b.block->Count(b.id):1);++i) { auto* ctrl=b.block->GetControllerByID(b.id,i); if(ctrl&&ctrl->IsAnimated()) Fail("CONTROLLED_PARAMETER","Animated/controlled parameter requires an explicit animation tool."); }
            json sharingEvidence;
            if(edit.value("sharing","")!="all_instances"&&Shared(owner,&sharingEvidence))
                Fail("SHARED_RESOURCE","Property owner is shared; inspect the reported consumers or explicitly use all_instances.",sharingEvidence);
            planned.push_back({owner,edit,Read(b,t)});
        }
    }
    Pin previousEnv(ip->GetEnvironmentMap()); const BOOL previousOn=ip->GetUseEnvironmentMap();
    if(p.contains("environment")) {
        if(!p.contains("expected_context")||p["expected_context"].is_null()) Fail("STALE_CONTEXT","Environment bindings require an inspected context token.");
        if(ip->GetEnvironmentMap()&&!p["environment"].value("replace_existing",false)) Fail("ENVIRONMENT_EXISTS","Replacing an existing environment requires replace_existing.");
    }
    std::vector<std::unique_ptr<Pin>> pins; json proofs=json::array();
    theHold.Begin(); bool committed=false;
    try {
        for(const auto& item:creates) {
            auto id=item["id"].get<std::string>(); auto* cd=classes.at(id);
            INode* madeNode=UsesPhotometricNodeMaker(cd)?CreatePhotometricNode(cd):nullptr;
            auto* value=madeNode?static_cast<ReferenceTarget*>(madeNode->GetObjectRef()):
                static_cast<ReferenceTarget*>(ip->CreateInstance(cd->SuperClassID(),cd->ClassID()));
            if(!value) Fail("CREATE_FAILED","Plugin did not construct an instance.");
            pins.push_back(std::make_unique<Pin>(value)); created[id]=value;
            if(value->SuperClassID()!=cd->SuperClassID()||value->ClassID()!=cd->ClassID()) Fail("CREATE_FAILED","Constructed instance does not match its descriptor.");
            if(cd->SuperClassID()==LIGHT_CLASS_ID) {
                auto* node=madeNode?madeNode:ip->CreateObjectNode(static_cast<Object*>(value)); if(!node) Fail("CREATE_FAILED","Failed to create a light node."); nodes[id]=node; nodeHandles.push_back(Animatable::GetHandleByAnim(node));
                const auto name=item.value("name",""); if(!name.empty()) { auto w=Utf8ToWide(name); node->SetName(w.c_str()); }
                if(item.contains("matrix")) { Matrix3 tm(TRUE); for(int r=0;r<4;++r) tm.SetRow(r,Point3(item["matrix"][r][0].get<float>(),item["matrix"][r][1].get<float>(),item["matrix"][r][2].get<float>())); node->SetNodeTM(t,tm); }
            }
        }
        for(const auto& item:planned) {
            auto* owner=item.existing?item.existing:ResolvePlanOwner(item.edit["owner_ref"],created);
            auto b=ResolveProperty(owner,item.edit["property_ref"]); Set(b,item.edit["value"],t,created);
        }
        if(p.contains("environment")) {
            const auto env=p["environment"]; auto* value=ResolvePlanOwner(env.at("owner_ref"),created);
            if(value->SuperClassID()!=TEXMAP_CLASS_ID) Fail("BAD_REFERENCE","Environment must reference a texture map.");
            auto* map=static_cast<Texmap*>(value); McpHost::UndoRecords::PutEnvironmentRestore(static_cast<Texmap*>(previousEnv.GetReference(0)),previousOn,map,TRUE);
            ip->SetEnvironmentMap(map); ip->SetUseEnvironmentMap(TRUE);
            if(ip->GetEnvironmentMap()!=map||!ip->GetUseEnvironmentMap()) Fail("VERIFY_MISMATCH","Environment binding did not apply.");
        }
        for(const auto& item:planned) {
            auto* owner=item.existing?item.existing:ResolvePlanOwner(item.edit["owner_ref"],created);
            const auto actual=Read(ResolveProperty(owner,item.edit["property_ref"]),t),expected=ExpectedValue(item.edit["value"],created);
            if(!Equal(actual,expected)) Fail("VERIFY_MISMATCH","Parameter readback differs from the requested value.",{{"property_ref",item.edit["property_ref"]},{"expected",expected},{"actual",actual}});
            proofs.push_back({{"owner_ref",OwnerRef(owner)},{"property_ref",item.edit["property_ref"]},{"before",item.before},{"actual",actual},{"matched",true}});
        }
        json resources=json::array();
        for(const auto& entry:created) { json resource={{"id",entry.first},{"owner_ref",OwnerRef(entry.second)}};
            if(nodes.count(entry.first)) { auto* node=nodes[entry.first]; resource["node_ref"]={{"handle",NodeHandle(node)},{"name",WideToUtf8(node->GetName())}}; json tm=json::array(); for(int r=0;r<4;++r) { auto v=node->GetNodeTM(t).GetRow(r); tm.push_back({v.x,v.y,v.z}); } resource["matrix"]=tm;
                for(const auto& spec:creates) if(spec["id"]==entry.first&&spec.contains("matrix")&&!Equal(tm,spec["matrix"])) Fail("VERIFY_MISMATCH","Light world transform differs from its requested matrix."); }
            resources.push_back(resource);
        }
        json result={{"status","applied"},{"resources",resources},{"properties",proofs},{"verification","typed_readback_matched"},{"transaction","one_native_hold"},{"context",Context()}};
        McpHost::DialogWatch::ThrowIfDismissed();
        theHold.Accept(_M("MCP Plugin Edit")); committed=true;
        McpHost::SceneJournal::AppendSynthetic("plugin_patch",{{"created",resources.size()},{"properties",proofs.size()}});
        ip->RedrawViews(t);
        return result;
    } catch(...) {
        if(committed) Fail("POST_COMMIT_FAILED","Changes committed, but response finalization failed. Inspect before retrying.");
        auto error=std::current_exception(); bool restored=true;
        try { theHold.Cancel(); } catch(...) { restored=false; }
        try {
            for(const auto& item:planned) if(item.existing&&!Equal(Read(ResolveProperty(item.existing,item.edit["property_ref"]),t),item.before)) restored=false;
            if(ip->GetEnvironmentMap()!=previousEnv.GetReference(0)||ip->GetUseEnvironmentMap()!=previousOn) restored=false;
            for(auto handle:nodeHandles) if(Animatable::GetAnimByHandle(handle)) restored=false;
        } catch(...) { restored=false; }
        if(!restored) Fail("ROLLBACK_FAILED","The edit failed and complete restoration could not be verified. Inspect before retrying.");
        std::rethrow_exception(error);
    }
}
}
