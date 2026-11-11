// Render jobs and render effects.
//
// Lights, the sun, the environment and render settings are not here: render_setup
// builds and reads them through the renderer presets (maxmcp/assets/renderers), the
// one place that knows what each renderer calls them.
//
// mx.render() queues a production render that starts once the script that
// queued it returns (or at job.wait()). A done file outside Max records the
// outcome, so render_automations(action='status') sees it as well.
#include "mx_common.h"

#include "mcp_bridge/node_ref.h"

#include <lslights.h>
#include <sfx.h>
#include <psapi.h>

#include <chrono>
#include <filesystem>
#include <fstream>
#include <functional>
#include <map>
#include <memory>
#include <mutex>
#include <random>
#include <sstream>
#include <thread>

namespace mx {
inline namespace MX_ABI_TAG {

using json = nlohmann::json;
namespace fs = std::filesystem;

static std::string Cls(Animatable* anim) { return HandlerHelpers::MaxScriptVisibleClassName(anim); }

static nb::object MaybeText(const std::string& text) { return text.empty() ? nb::none() : nb::cast(text); }

// ── Render jobs ──────────────────────────────────────────────────

using BridgeCall = const char* (*)(const char* request, size_t length, size_t* responseLength);

// The bridge's in-process entry point. NativeJson refuses to run off the main
// thread, and render workers call the bridge from their own thread.
static BridgeCall BridgeEntry() {
    std::vector<HMODULE> modules(512);
    DWORD needed = 0;
    HANDLE process = GetCurrentProcess();
    while (K32EnumProcessModules(process, modules.data(), DWORD(modules.size() * sizeof(HMODULE)), &needed) &&
           needed > modules.size() * sizeof(HMODULE))
        modules.resize(needed / sizeof(HMODULE));
    modules.resize(needed / sizeof(HMODULE));
    for (HMODULE module : modules)
        if (auto call = reinterpret_cast<BridgeCall>(GetProcAddress(module, "mcp_bridge_call"))) return call;
    return nullptr;
}

enum class Phase { Queued, Inline, Posted, Done, Failed, Cancelled };

struct RenderState {
    std::mutex mutex;
    std::string id, output;
    fs::path signal;
    int width = 0, height = 0;
    Phase phase = Phase::Queued;
    double seconds = 0;
    std::string error;
};

struct RenderJob { std::string id; };

static std::vector<std::shared_ptr<RenderState>>& Jobs() {  // main thread only
    static std::vector<std::shared_ptr<RenderState>> jobs;
    return jobs;
}

static std::shared_ptr<RenderState> FindJob(const RenderJob& j) {
    RequireMain();
    for (const auto& job : Jobs())
        if (job->id == j.id) return job;
    throw NotFound("render job '" + j.id + "' is not from this 3ds Max session");
}

static const char* StatusName(Phase phase) {
    switch (phase) {
    case Phase::Queued: return "queued";
    case Phase::Inline: case Phase::Posted: return "rendering";
    case Phase::Done: return "done";
    case Phase::Failed: return "failed";
    default: return "cancelled";
    }
}

static fs::path SignalDir() {
    wchar_t buffer[MAX_PATH];
    const DWORD n = GetEnvironmentVariableW(L"LOCALAPPDATA", buffer, MAX_PATH);
    if (n && n < MAX_PATH) return fs::path(buffer) / "3dsmax-mcp" / "render_signals";
    const DWORD home = GetEnvironmentVariableW(L"USERPROFILE", buffer, MAX_PATH);
    return fs::path(home && home < MAX_PATH ? buffer : L".") / "AppData" / "Local" / "3dsmax-mcp" / "render_signals";
}

// The done file render_automations(action='status', job_id=...) reads: written
// to a temporary name, then renamed, so a reader never sees half of it.
static void WriteSignal(const RenderState& job) {
    json doc = {{"job_id", job.id}, {"status", job.phase == Phase::Done ? "complete" : StatusName(job.phase)},
                {"complete", job.phase == Phase::Done}, {"output", job.output}, {"width", job.width},
                {"height", job.height}, {"seconds", job.seconds}, {"source", "_mx"}};
    if (!job.error.empty()) doc["error"] = job.error;
    std::error_code ec;
    fs::create_directories(job.signal.parent_path(), ec);
    fs::path temp = job.signal;
    temp += L".tmp";
    {
        std::ofstream file(temp, std::ios::binary | std::ios::trunc);
        if (!file) return;
        file << doc.dump();
    }
    MoveFileExW(temp.c_str(), job.signal.c_str(), MOVEFILE_REPLACE_EXISTING);
}

static double SecondsSince(std::chrono::steady_clock::time_point start) {
    return std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count();
}

// Settles a job that is still in `from` (another path may have settled it).
static void Finish(RenderState& job, std::initializer_list<Phase> from, const std::string& error, double seconds) {
    std::lock_guard<std::mutex> lock(job.mutex);
    if (std::find(from.begin(), from.end(), job.phase) == from.end()) return;
    job.phase = error.empty() ? Phase::Done : Phase::Failed;
    job.error = error;
    job.seconds = seconds;
    WriteSignal(job);
}

static json RenderArgs(const RenderState& job) {
    return {{"width", job.width}, {"height", job.height}, {"output_path", job.output}};
}

static std::string ErrorText(const json& error) {
    json payload = error;
    if ((error.is_string)()) {
        payload = json::parse(error.get<std::string>(), nullptr, false);
        if (!payload.is_object()) return error.get<std::string>();
    }
    if (payload.is_object() && payload.contains("message") && (payload["message"].is_string)())
        return payload["message"].get<std::string>();
    return error.dump();
}

// One request as a pipe client sends it. Max drops a request that waited past
// its executor's timeout without starting (the main thread stayed busy); that
// one is sent again, since it never ran.
static void SendUntilStarted(BridgeCall call, const std::string& type, const json& args, const std::string& tag) {
    for (int attempt = 1;; ++attempt) {
        const json request = {{"command", type == "maxscript" ? args.value("code", std::string()) : args.dump()},
                              {"type", type}, {"requestId", tag + "-" + std::to_string(attempt)}, {"protocolVersion", 2}};
        const std::string text = request.dump();
        size_t length = 0;
        const char* raw = call(text.c_str(), text.size(), &length);
        const json response = json::parse(raw ? std::string(raw, length) : std::string(), nullptr, false);
        if (!response.is_object()) throw std::runtime_error(type + ": unreadable reply from the bridge");
        if (response.value("success", false)) return;
        const std::string error = ErrorText(response.value("error", json()));
        if (error.find("before starting") == std::string::npos) throw std::runtime_error(error);
    }
}

static void RenderWhenFree(std::shared_ptr<RenderState> job, BridgeCall call) {
    const std::string tag = "mx-render-" + job->id;
    try {
        // A no-op queued on Max's main thread behind the script that called
        // mx.render(): once it ran, that script has returned.
        SendUntilStarted(call, "maxscript", {{"code", "ok"}}, tag + "-after");
        {
            std::lock_guard<std::mutex> lock(job->mutex);
            if (job->phase != Phase::Queued) return;  // wait() rendered it, or cancel() dropped it
            job->phase = Phase::Posted;
        }
        const auto start = std::chrono::steady_clock::now();
        SendUntilStarted(call, "native:render_scene", RenderArgs(*job), tag);
        Finish(*job, {Phase::Posted}, "", SecondsSince(start));
    } catch (const std::exception& e) {
        Finish(*job, {Phase::Queued, Phase::Posted}, e.what(), 0);
    }
}

static std::string NewJobId() {
    static std::mt19937_64 random{std::random_device{}()};
    static const char* hex = "0123456789abcdef";
    std::string id;
    for (int i = 0; i < 12; ++i) id += hex[random() & 15];
    return id;
}

static std::string OutputPath(const std::string& output) {
    if (output.empty()) return "";
    std::error_code ec;
    const fs::path full = fs::absolute(fs::path(Utf8ToWide(output)), ec);
    if (ec || !fs::is_directory(full.parent_path(), ec))
        throw BadParam("the output folder of '" + output + "' does not exist; create it first");
    if (full.extension().empty()) throw BadParam("output needs an image extension: .png, .exr, .jpg, .tif");
    return WideToUtf8(full.c_str());
}

static RenderJob QueueRender(std::optional<int> width, std::optional<int> height, const std::string& output) {
    RequireMain();
    Interface* ip = GetCOREInterface();
    if (GetCOREInterface8()->IsRenderActive()) throw MaxError("a render is running; wait for it before queuing another");
    if (!ip->GetRenderer(RS_Production)) throw MaxError("no production renderer is assigned; pick one in Render Setup");
    auto job = std::make_shared<RenderState>();
    job->width = width.value_or(ip->GetRendWidth());
    job->height = height.value_or(ip->GetRendHeight());
    if (job->width < 1 || job->height < 1 || job->width > 32768 || job->height > 32768)
        throw BadParam("width and height must be 1-32768 pixels");
    job->output = OutputPath(output);
    const BridgeCall call = BridgeEntry();
    if (!call) throw MaxError("the MCP bridge plugin is not loaded in this 3ds Max");
    job->id = NewJobId();
    job->signal = SignalDir() / (job->id + ".done.json");
    auto& jobs = Jobs();
    // Keep the last 64 jobs, dropping the oldest settled ones first.
    for (auto it = jobs.begin(); jobs.size() >= 64 && it != jobs.end();) {
        Phase phase;
        {
            std::lock_guard<std::mutex> lock((*it)->mutex);
            phase = (*it)->phase;
        }
        const bool settled = phase == Phase::Done || phase == Phase::Failed || phase == Phase::Cancelled;
        it = settled ? jobs.erase(it) : it + 1;
    }
    jobs.push_back(job);
    std::thread(RenderWhenFree, job, call).detach();
    return RenderJob{job->id};
}

static nb::dict ResultDict(const RenderState& job) {
    nb::dict out;
    out["status"] = StatusName(job.phase);
    out["output"] = job.output.empty() ? nb::none() : nb::cast(job.output);
    out["width"] = job.width;
    out["height"] = job.height;
    out["seconds"] = job.seconds;
    return out;
}

static void RenderInline(RenderState& job) {
    std::string error;
    const auto start = std::chrono::steady_clock::now();
    {
        nb::gil_scoped_release release;  // other Python threads run on during the render
        try {
            NativeJson("native:render_scene", RenderArgs(job));
        } catch (const std::exception& e) {
            error = e.what();
        }
    }
    Finish(job, {Phase::Inline}, error, SecondsSince(start));
}

static nb::dict WaitJob(const RenderJob& j) {
    const auto job = FindJob(j);
    Phase phase;
    {
        std::lock_guard<std::mutex> lock(job->mutex);
        phase = job->phase;
        if (phase == Phase::Queued) job->phase = Phase::Inline;
    }
    if (phase == Phase::Queued) RenderInline(*job);
    else if (phase == Phase::Inline || phase == Phase::Posted)
        throw MaxError("render '" + j.id + "' is handed to Max's main thread and runs after this script returns; read "
                       "job.status from a later script, or render_automations(action='status', job_id='" + j.id +
                       "') from outside Max");
    std::lock_guard<std::mutex> lock(job->mutex);
    if (job->phase == Phase::Failed) throw MaxError("render '" + j.id + "' failed: " + job->error);
    if (job->phase == Phase::Cancelled) throw MaxError("render '" + j.id + "' was cancelled before it started");
    return ResultDict(*job);
}

static bool CancelJob(const RenderJob& j) {
    const auto job = FindJob(j);
    std::lock_guard<std::mutex> lock(job->mutex);
    if (job->phase != Phase::Queued) return false;
    job->phase = Phase::Cancelled;
    WriteSignal(*job);
    return true;
}

template <typename F>
static auto WithJob(const RenderJob& j, F&& read) {
    const auto job = FindJob(j);
    std::lock_guard<std::mutex> lock(job->mutex);
    return read(*job);
}

static std::string JobRepr(const RenderJob& j) {
    try {
        return WithJob(j, [](const RenderState& s) {
            return "<RenderJob " + s.id + " " + StatusName(s.phase) + " " + std::to_string(s.width) + "x" +
                   std::to_string(s.height) + (s.output.empty() ? "" : " -> " + s.output) + ">";
        });
    } catch (const NotFound&) {
        return "<RenderJob " + j.id + " unknown>";
    }
}

// ── Render effects ───────────────────────────────────────────────

struct EffectRef { AnimHandle handle = 0; };

struct Placed {
    SpecialFX* fx = nullptr;
    bool atmospheric = false;
    int index = 0;  // 0-based in its list
};

static std::optional<Placed> Place(AnimHandle handle) {
    Interface* ip = GetCOREInterface();
    for (int i = 0; i < ip->NumAtmospheric(); ++i)
        if (Atmospheric* a = ip->GetAtmospheric(i); a && Animatable::GetHandleByAnim(a) == handle) return Placed{a, true, i};
    for (int i = 0; i < ip->NumEffects(); ++i)
        if (Effect* e = ip->GetEffect(i); e && Animatable::GetHandleByAnim(e) == handle) return Placed{e, false, i};
    return std::nullopt;
}

static Placed ResolveEffect(const EffectRef& r) {
    RequireMain();
    if (auto placed = Place(r.handle)) return *placed;
    throw NotFound("effect #" + std::to_string(r.handle) + " was deleted");
}

// The instance's own name (SpecialFX::name, MAXScript .name), else the
// plug-in's name: GetName() names the plug-in, not this effect.
static std::string EffectName(SpecialFX* fx) {
    std::string name = WideToUtf8(fx->name.data());
    if (name.empty()) name = WideToUtf8(fx->GetName(false).data());
    return name.empty() ? Cls(fx) : name;
}

static std::vector<EffectRef> SceneEffects(const std::optional<std::string>& kind) {
    RequireMain();
    if (kind && *kind != "atmospheric" && *kind != "render") throw BadParam("kind must be 'atmospheric' or 'render'");
    Interface* ip = GetCOREInterface();
    std::vector<EffectRef> out;
    if (!kind || *kind == "atmospheric")
        for (int i = 0; i < ip->NumAtmospheric(); ++i)
            if (Atmospheric* a = ip->GetAtmospheric(i)) out.push_back(EffectRef{Animatable::GetHandleByAnim(a)});
    if (!kind || *kind == "render")
        for (int i = 0; i < ip->NumEffects(); ++i)
            if (Effect* e = ip->GetEffect(i)) out.push_back(EffectRef{Animatable::GetHandleByAnim(e)});
    return out;
}

static void SetEffectActive(const EffectRef& r, bool on) {
    const Placed p = ResolveEffect(r);
    // setActive is MAXScript's switch; the bridge's handler runs it by index.
    NativeJson("native:toggle_effect",
               {{"index", p.index + 1}, {"effect_type", p.atmospheric ? "atmospheric" : "render"}, {"active", on}});
    if ((ResolveEffect(r).fx->Active(Now()) != 0) != on)
        throw MaxError("3ds Max kept '" + EffectName(p.fx) + "' " + (on ? "inactive" : "active"));
}

static void DeleteEffect(const EffectRef& r) {
    const Placed p = ResolveEffect(r);
    if (p.atmospheric) GetCOREInterface()->DeleteAtmosphere(p.index);
    else GetCOREInterface()->DeleteEffect(p.index);
}

static std::string EffectRepr(const EffectRef& r) {
    const auto p = Place(r.handle);
    if (!p) return "<Effect #" + std::to_string(r.handle) + " deleted>";
    return "<Effect '" + EffectName(p->fx) + "' " + Cls(p->fx) + (p->atmospheric ? " atmospheric" : " render") +
           (p->fx->Active(Now()) ? " active" : " inactive") + ">";
}

// ── Binding ──────────────────────────────────────────────────────

static void BindRender(nb::module_& m) {
    nb::class_<RenderJob>(m, "RenderJob",
        "A production render queued by mx.render(): job.wait() renders it now; else it starts when the script returns.")
        .def_prop_ro("id", [](const RenderJob& j) { return j.id; },
            "The job's ID, as render_automations(job_id=...) takes it.")
        .def_prop_ro("status", [](const RenderJob& j) { return WithJob(j, [](const RenderState& s) { return std::string(StatusName(s.phase)); }); },
            "'queued', 'rendering', 'done', 'failed' or 'cancelled'.")
        .def_prop_ro("done", [](const RenderJob& j) {
            return WithJob(j, [](const RenderState& s) { return s.phase == Phase::Done || s.phase == Phase::Failed || s.phase == Phase::Cancelled; });
        }, "True once the job ended (done, failed or cancelled).")
        .def_prop_ro("result", [](const RenderJob& j) {
            return WithJob(j, [](const RenderState& s) { return s.phase == Phase::Done ? nb::object(ResultDict(s)) : nb::none(); });
        }, "{'status', 'output', 'width', 'height', 'seconds'} once done, else None.")
        .def_prop_ro("error", [](const RenderJob& j) {
            return WithJob(j, [](const RenderState& s) { return MaybeText(s.error); });
        }, "Why the render failed, else None.")
        .def_prop_ro("signal_path", [](const RenderJob& j) {
            return WithJob(j, [](const RenderState& s) { return WideToUtf8(s.signal.c_str()); });
        }, "Done file written when the job ends; render_automations(action='status', job_id=job.id) reads it outside Max.")
        .def("wait", &WaitJob,
            "Render now if still queued and return the result; raises if it failed, was cancelled, or already went to Max.")
        .def("cancel", &CancelJob, "Drop the job if it has not started; returns whether it was dropped.")
        .def("__repr__", &JobRepr)
        .def("__eq__", [](const RenderJob& a, nb::handle b) { return nb::isinstance<RenderJob>(b) && a.id == nb::cast<RenderJob>(b).id; }, nb::arg("other").none())
        .def("__hash__", [](const RenderJob& j) { return (Py_hash_t)std::hash<std::string>{}(j.id); });

    m.def("render", &QueueRender, "width"_a = nb::none(), "height"_a = nb::none(), "output"_a = "",
          "Queue a production render of the active view at the time slider's frame; it starts when this script returns, "
          "or now with .wait(): mx.render(output='C:/r/a.png').wait(). Size defaults to Render Setup's.");
    m.def("render_jobs", [] {
        RequireMain();
        std::vector<RenderJob> out;
        for (const auto& job : Jobs()) out.push_back(RenderJob{job->id});
        return out;
    }, "Render jobs queued in this Max session, oldest first.");
}

static void BindEffects(nb::module_& m) {
    nb::class_<EffectRef>(m, "Effect", "An atmospheric or render effect: fx.active = False; fx.params.density = 20.")
        .def_prop_ro("name", [](const EffectRef& r) { return EffectName(ResolveEffect(r).fx); },
            "Name in the Environment or Effects list.")
        .def_prop_ro("class_name", [](const EffectRef& r) { return Cls(ResolveEffect(r).fx); },
            "MAXScript class: VolumeFog, Blur, Lens_Effects, ...")
        .def_prop_ro("kind", [](const EffectRef& r) { return ResolveEffect(r).atmospheric ? "atmospheric" : "render"; },
            "'atmospheric' (Rendering > Environment) or 'render' (Rendering > Effects).")
        .def_prop_ro("index", [](const EffectRef& r) { return ResolveEffect(r).index + 1; }, "1-based position in its list.")
        .def_prop_rw("active", [](const EffectRef& r) { return ResolveEffect(r).fx->Active(Now()) != 0; }, &SetEffectActive,
            "Whether the effect renders.")
        .def_prop_ro("nodes", [](const EffectRef& r) {
            SpecialFX* fx = ResolveEffect(r).fx;
            std::vector<Node> out;
            for (int i = 0; i < fx->NumGizmos(); ++i)
                if (INode* node = fx->GetGizmo(i); InScene(node)) out.push_back(Wrap(node));
            return out;
        }, "Gizmo nodes the effect uses (atmospheric apparatus); empty for most render effects.")
        .def_prop_ro("params", [](const EffectRef& r) { ResolveEffect(r); return Params{0, r.handle}; },
            "The effect's parameters.")
        .def("delete", &DeleteEffect, "Remove the effect from the scene.")
        .def("__repr__", &EffectRepr)
        .def("__eq__", [](const EffectRef& a, nb::handle b) { return nb::isinstance<EffectRef>(b) && a.handle == nb::cast<EffectRef>(b).handle; }, nb::arg("other").none())
        .def("__hash__", [](const EffectRef& r) { return (Py_hash_t)r.handle; });

    m.def("effects", &SceneEffects, "kind"_a = nb::none(),
          "Atmospherics then render effects, or one kind: effects('atmospheric').");
}

void BindRendering(nb::module_& m) {
    BindRender(m);
    BindEffects(m);
}

} // namespace MX_ABI_TAG
} // namespace mx
