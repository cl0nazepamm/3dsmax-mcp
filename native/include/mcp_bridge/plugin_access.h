#pragma once
#include "mcp_bridge/handler_helpers.h"
#include <ifnpub.h>

// All entry points require Max's main thread. No plugin objects are constructed
// to discover metadata. The wire format deliberately uses IDs, not script code.
namespace PluginAccess {
using json = nlohmann::json;
int BaseType(int type);
std::string TypeName(int type);
json DescribeParameter(ParamBlockDesc2* block, const ParamDef& parameter);
json DescribeInterface(FPInterface* interfaceValue);
json Inspect(const json& request);
json Context();
json Apply(const json& request);
// Make the class in request.class_ref the production renderer. Not undoable, as in Max itself.
json AssignRenderer(const json& request);
}
