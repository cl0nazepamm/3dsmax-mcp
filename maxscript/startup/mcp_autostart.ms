/*
	MCP Server Auto-Start
	Place this file in: [3ds Max Install Dir]\scripts\startup\
	Also copy mcp_server.ms to: [3ds Max Install Dir]\scripts\mcp\mcp_server.ms
*/

(
	local scriptDir = (getDir #scripts) + "\\mcp\\"
	local serverScript = scriptDir + "mcp_server.ms"

	if doesFileExist serverScript then
	(
		fileIn serverScript
		format "MCP: Auto-start complete\n"
	)
	else
	(
		format "MCP: Server script not found at: %\n" serverScript
		format "MCP: Copy mcp_server.ms to the path above for auto-start\n"
	)
)
