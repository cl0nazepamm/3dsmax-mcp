/*
	3ds Max MCP Server - TCP Socket v1.0
	Non-blocking TCP command server for Model Context Protocol bridge

	Usage:
		MCP_Server = MCPServerStruct()
		MCP_Server.start()
		MCP_Server.stop()

	Protocol: JSON over TCP on port 8765
	- Client connects, sends JSON + newline
	- Server executes, returns JSON + newline, closes connection
*/

global MCP_Server

struct MCPServerStruct
(
	timer = undefined,
	listener = undefined,
	isRunning = false,
	port = 8765,
	safeMode = true,
	protocolVersion = 2,
	idlePollIntervalMs = 250,
	activePollIntervalMs = 50,
	activePollTicksRemaining = 0,
	activePollTicksAfterRequest = 10,

	fn nativeBridgeAvailable =
	(
		try
		(
			local hwnds = windows.getChildHWND 0 "MCPBridgeExecutor"
			hwnds != undefined and hwnds.count > 0
		)
		catch
		(
			false
		)
	),

	-- Load safe_mode from shared config: %LOCALAPPDATA%\3dsmax-mcp\mcp_config.ini
	fn loadSafeMode =
	(
		local iniPath = (dotNetClass "System.Environment").GetFolderPath (dotNetClass "System.Environment+SpecialFolder").LocalApplicationData
		iniPath += "\\3dsmax-mcp\\mcp_config.ini"
		local val = "true"
		try (val = getINISetting iniPath "mcp" "safe_mode") catch ()
		if val == "" do val = "true"
		val = toLower (trimLeft (trimRight val))
		not (val == "false" or val == "0" or val == "off")
	),

	-- Parse a hex string (e.g. "00e9") to integer for \uXXXX decoding
	fn hexToInt hexStr =
	(
		local val = 0
		for i = 1 to hexStr.count do
		(
			local c = bit.charAsInt (toLower hexStr[i])
			local d = if c >= 48 and c <= 57 then c - 48
					  else if c >= 97 and c <= 102 then c - 87
					  else 0
			val = val * 16 + d
		)
		val
	),

	-- Escape a string for safe JSON embedding (handles all JSON-required escapes)
	fn escapeJsonString str =
	(
		if str == undefined then return ""
		local s = str as string
		-- Fast path: common escapes via native substituteString
		s = substituteString s "\\" "\\\\"
		s = substituteString s "\"" "\\\""
		s = substituteString s "\n" "\\n"
		s = substituteString s "\r" "\\r"
		s = substituteString s "\t" "\\t"
		-- Check for remaining control chars (backspace, form feed, etc.)
		local hasControl = false
		for i = 1 to s.count while not hasControl do
		(
			local code = bit.charAsInt s[i]
			if code < 32 do hasControl = true
		)
		if hasControl then
		(
			local result = ""
			local hexChars = "0123456789abcdef"
			for i = 1 to s.count do
			(
				local code = bit.charAsInt s[i]
				if code == 8 then result += "\\b"
				else if code == 12 then result += "\\f"
				else if code < 32 then
				(
					local hi = (mod (bit.shift code -4) 16) as integer + 1
					local lo = (mod code 16) as integer + 1
					result += "\\u00" + hexChars[hi] + hexChars[lo]
				)
				else result += s[i]
			)
			s = result
		)
		s
	),

	-- Build a JSON response string
	fn buildResponse success result errorMsg requestId:"" startedAt:0 cmdType:"maxscript" =
	(
		local safeResult = escapeJsonString (if result != undefined then (result as string) else "")
		local safeError = escapeJsonString errorMsg
		local successStr = if success then "true" else "false"
		local safeReqId = escapeJsonString requestId
		local safeCmdType = escapeJsonString cmdType
		local reqField = if requestId != "" then ("\"" + safeReqId + "\"") else "null"
		local durationMs = amax #(0, (timeStamp() - startedAt))

		"{" + \
			"\"success\":" + successStr + "," + \
			"\"requestId\":" + reqField + "," + \
			"\"result\":\"" + safeResult + "\"," + \
			"\"error\":\"" + safeError + "\"," + \
			"\"meta\":{" + \
				"\"protocolVersion\":" + (protocolVersion as string) + "," + \
				"\"cmdType\":\"" + safeCmdType + "\"," + \
				"\"safeMode\":" + (if safeMode then "true" else "false") + "," + \
				"\"durationMs\":" + (durationMs as string) + \
			"}" + \
		"}"
	),

	-- Extract a JSON string value by key (simple parser)
	fn extractJsonValue jsonStr key =
	(
		searchKey = "\"" + key + "\""
		keyPos = findString jsonStr searchKey
		if keyPos == undefined then return ""

		colonPos = findString (substring jsonStr (keyPos + searchKey.count) -1) ":"
		if colonPos == undefined then return ""
		startPos = keyPos + searchKey.count + colonPos

		while startPos <= jsonStr.count and (jsonStr[startPos] == " " or jsonStr[startPos] == "\t") do
			startPos += 1

		if startPos > jsonStr.count then return ""

		if jsonStr[startPos] == "\"" then
		(
			pos = startPos + 1
			result = ""
			while pos <= jsonStr.count do
			(
				if jsonStr[pos] == "\\" and pos + 1 <= jsonStr.count then
				(
					nextChar = jsonStr[pos + 1]
					if nextChar == "\"" then (result += "\""; pos += 2)
					else if nextChar == "\\" then (result += "\\"; pos += 2)
					else if nextChar == "n" then (result += "\n"; pos += 2)
					else if nextChar == "r" then (result += "\r"; pos += 2)
					else if nextChar == "t" then (result += "\t"; pos += 2)
					else if nextChar == "b" then (result += (bit.intAsChar 8); pos += 2)
					else if nextChar == "f" then (result += (bit.intAsChar 12); pos += 2)
					else if nextChar == "/" then (result += "/"; pos += 2)
					else if nextChar == "u" and pos + 5 <= jsonStr.count then
					(
						local hexStr = substring jsonStr (pos + 2) 4
						local codepoint = hexToInt hexStr
						if codepoint > 0 then
						(
							if codepoint <= 255 then
								result += bit.intAsChar codepoint
							else
								try (result += ((dotNetClass "System.Char").ConvertFromUtf32 codepoint))
								catch (result += "?")
						)
						pos += 6
					)
					else (result += jsonStr[pos]; pos += 1)
				)
				else if jsonStr[pos] == "\"" then
				(
					exit
				)
				else
				(
					result += jsonStr[pos]
					pos += 1
				)
			)
			return result
		)
		else
		(
			pos = startPos
			while pos <= jsonStr.count and jsonStr[pos] != "," and jsonStr[pos] != "}" do
				pos += 1
			return (trimRight (trimLeft (substring jsonStr startPos (pos - startPos))))
		)
	),

	-- Process a request and return JSON response
	fn processRequest jsonString =
	(
		local response = ""
		local startedAt = timeStamp()

		try
		(
			local command = extractJsonValue jsonString "command"
			local cmdType = extractJsonValue jsonString "type"
			local requestId = extractJsonValue jsonString "requestId"

			if cmdType == "" do cmdType = "maxscript"

			if cmdType != "ping" and command == "" then
			(
				return (buildResponse false undefined "Empty command" requestId:requestId startedAt:startedAt cmdType:cmdType)
			)

			local result = undefined
			local success = true
			local errorMsg = ""
			local savedSafeMode = safeMode

			try
			(
				if cmdType == "ping" then
				(
					local maxYear = ((1998 + ((maxVersion())[1] / 1000)) as integer)
					local rendererName = try ((classOf renderers.current) as string) catch "unknown"
					result = "{\"pong\":true" + \
						",\"server\":\"3dsmax-mcp\"" + \
						",\"protocolVersion\":" + (protocolVersion as string) + \
						",\"maxVersion\":" + (maxYear as string) + \
						",\"renderer\":\"" + (escapeJsonString rendererName) + "\"" + \
						",\"objectCount\":" + (objects.count as string) + \
						",\"selectionCount\":" + (selection.count as string) + \
						",\"safeMode\":" + (if safeMode then "true" else "false") + \
						",\"port\":" + (port as string) + "}"
				)
				else if cmdType == "maxscript" then
				(
					if safeMode then
					(
						-- Block dangerous commands
						local lowerCmd = toLower command
						local blocked = #("doscommand", "shelllaunch", "deletefile", "python.execute", "createfile", "hiddendoscommand")
						local isBlocked = false
						for b in blocked while not isBlocked do
							if findString lowerCmd b != undefined do isBlocked = true
						if isBlocked then
						(
							success = false
							errorMsg = "Blocked by safe mode. Set safe_mode=false in %LOCALAPPDATA%\\3dsmax-mcp\\mcp_config.ini to disable."
						)
						else
							result = execute command
					)
					else
						result = execute command
				)
				else if cmdType == "python" then
				(
					if safeMode then
					(
						success = false
						errorMsg = "Python execution blocked by safe mode"
					)
					else
					(
						python.Execute command
						result = "Python executed"
					)
				)
				else
				(
					success = false
					errorMsg = "Unknown command type: " + cmdType
				)
			)
			catch
			(
				success = false
				errorMsg = getCurrentException()
			)

			-- Restore safeMode so commands cannot tamper with it
			safeMode = savedSafeMode

			response = buildResponse success result errorMsg requestId:requestId startedAt:startedAt cmdType:cmdType
		)
		catch
		(
			response = buildResponse false undefined ("Parse error: " + (getCurrentException())) startedAt:startedAt
		)

		response
	),

	-- Handle a single client connection
	fn handleClient client =
	(
		try
		(
			stream = client.GetStream()
			reader = dotNetObject "System.IO.StreamReader" stream
			writer = dotNetObject "System.IO.StreamWriter" stream

			-- Read the request line
			requestLine = reader.ReadLine()

			-- Convert .NET string to MAXScript string
			requestStr = requestLine as string

			format "MCP: Received request: %\n" requestStr

			if requestStr != undefined and requestStr.count > 0 then
			(
				-- Process and send response
				responseStr = processRequest requestStr
				format "MCP: Sending response: %\n" responseStr
				writer.WriteLine responseStr
				writer.Flush()
			)
			else
			(
				format "MCP: Empty request received\n"
			)

			reader.Close()
			writer.Close()
			client.Close()
		)
		catch
		(
			format "MCP: Client error: %\n" (getCurrentException())
			try (client.Close()) catch ()
		)
	),

	fn setPollInterval intervalMs =
	(
		if timer != undefined and timer.Interval != intervalMs do timer.Interval = intervalMs
	),

	fn armActivePolling =
	(
		activePollTicksRemaining = activePollTicksAfterRequest
		setPollInterval activePollIntervalMs
	),

	fn decayPolling =
	(
		if activePollTicksRemaining > 0 then
		(
			activePollTicksRemaining -= 1
			if activePollTicksRemaining <= 0 do setPollInterval idlePollIntervalMs
		)
		else
		(
			setPollInterval idlePollIntervalMs
		)
	),

	-- Timer tick: check for pending connections
	fn onTick sender args =
	(
		if MCP_Server == undefined or not MCP_Server.isRunning then return()
		if MCP_Server.nativeBridgeAvailable() then
		(
			format "MCP: Native bridge detected; stopping TCP fallback\n"
			MCP_Server.stop()
			return()
		)

		try
		(
			-- Check if there's a pending connection (non-blocking)
			if MCP_Server.listener.Pending() then
			(
				MCP_Server.armActivePolling()
				client = MCP_Server.listener.AcceptTcpClient()
				MCP_Server.handleClient client
			)
			else
			(
				MCP_Server.decayPolling()
			)
		)
		catch
		(
			format "MCP: Tick error: %\n" (getCurrentException())
		)
	),

	-- Start the TCP server
	fn start =
	(
		if not isRunning then
		(
			if nativeBridgeAvailable() then
			(
				format "MCP: Native bridge detected; TCP fallback not started\n"
				return()
			)

			try
			(
				-- Create TCP listener
				ipAddress = (dotNetClass "System.Net.IPAddress").Parse "127.0.0.1"
				listener = dotNetObject "System.Net.Sockets.TcpListener" ipAddress port
				listener.Start()

				-- Create timer to poll for connections
				timer = dotNetObject "System.Windows.Forms.Timer"
				timer.Interval = idlePollIntervalMs  -- idle TCP fallback should stay quiet
				dotNet.setLifetimeControl timer #dotnet

				dotNet.addEventHandler timer "Tick" onTick
				timer.Start()
				isRunning = true

				format "MCP Server started (TCP on port %)\n" port
			)
			catch
			(
				format "MCP: Failed to start: %\n" (getCurrentException())
			)
		)
		else
		(
			format "MCP Server already running\n"
		)
	),

	-- Stop the server
	fn stop =
	(
		if isRunning then
		(
			try (timer.Stop()) catch ()
			try (dotNet.removeAllEventHandlers timer) catch ()
			try (listener.Stop()) catch ()
			isRunning = false
			format "MCP Server stopped\n"
		)
		else
		(
			format "MCP Server is not running\n"
		)
	),

	-- Restart the server
	fn restart =
	(
		stop()
		start()
	)
)

-- Auto-create and start
if MCP_Server != undefined and MCP_Server.isRunning then
(
	MCP_Server.stop()
)
MCP_Server = MCPServerStruct()
MCP_Server.safeMode = MCP_Server.loadSafeMode()
format "MCP: safe_mode = %\n" MCP_Server.safeMode
MCP_Server.start()
