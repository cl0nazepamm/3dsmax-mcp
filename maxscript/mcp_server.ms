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

	-- Escape a string for safe JSON embedding
	fn escapeJsonString str =
	(
		s = str as string
		s = substituteString s "\\" "\\\\"
		s = substituteString s "\"" "\\\""
		s = substituteString s "\n" "\\n"
		s = substituteString s "\r" "\\r"
		s = substituteString s "\t" "\\t"
		s
	),

	-- Build a JSON response string
	fn buildResponse success result errorMsg =
	(
		safeResult = escapeJsonString (if result != undefined then (result as string) else "")
		safeError = escapeJsonString errorMsg
		successStr = if success then "true" else "false"

		"{" + \
			"\"success\":" + successStr + "," + \
			"\"result\":\"" + safeResult + "\"," + \
			"\"error\":\"" + safeError + "\"" + \
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
		response = ""

		try
		(
			command = extractJsonValue jsonString "command"
			cmdType = extractJsonValue jsonString "type"

			if cmdType == "" do cmdType = "maxscript"

			if command == "" then
			(
				return (buildResponse false undefined "Empty command")
			)

			result = undefined
			success = true
			errorMsg = ""

			try
			(
				if cmdType == "maxscript" then
				(
					result = execute command
				)
				else if cmdType == "python" then
				(
					python.Execute command
					result = "Python executed"
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

			response = buildResponse success result errorMsg
		)
		catch
		(
			response = buildResponse false undefined ("Parse error: " + (getCurrentException()))
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

	-- Timer tick: check for pending connections
	fn onTick sender args =
	(
		if MCP_Server == undefined or not MCP_Server.isRunning then return()

		try
		(
			-- Check if there's a pending connection (non-blocking)
			if MCP_Server.listener.Pending() then
			(
				client = MCP_Server.listener.AcceptTcpClient()
				MCP_Server.handleClient client
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
			try
			(
				-- Create TCP listener
				ipAddress = (dotNetClass "System.Net.IPAddress").Parse "127.0.0.1"
				listener = dotNetObject "System.Net.Sockets.TcpListener" ipAddress port
				listener.Start()

				-- Create timer to poll for connections
				timer = dotNetObject "System.Windows.Forms.Timer"
				timer.Interval = 50  -- 50ms polling
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
MCP_Server.start()
