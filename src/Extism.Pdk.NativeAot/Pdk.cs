using System.Text;

namespace Extism.Pdk.NativeAot;

public static class Pdk
{
    public static byte[] GetInput()
    {
        var len = Imports.extism_input_length();
        var buf = new byte[len];
        var chunks = len / 8;

        for (ulong i = 0; i < chunks; i++)
        {
            var val = Imports.extism_input_load_u64(i * 8);
            BitConverter.TryWriteBytes(buf.AsSpan((int)(i * 8)), val);
        }

        for (var i = chunks * 8; i < len; i++)
        {
            buf[i] = Imports.extism_input_load_u8(i);
        }

        return buf;
    }

    public static string GetInputString() => Encoding.UTF8.GetString(GetInput());

    /// <summary>
    /// Allocates Extism memory, copies <paramref name="data"/> into it, and sets the output.
    /// </summary>
    public static void SetOutput(ReadOnlySpan<byte> data)
    {
        var block = Allocate(data);
        // Do not free — extism_output_set references this memory until the host reads it.
        Imports.extism_output_set(block.Offset, block.Length);
    }

    public static void SetOutput(string value) => SetOutput(Encoding.UTF8.GetBytes(value).AsSpan());

    /// <summary>
    /// Sets an already-allocated block as the output.
    /// The caller must NOT dispose <paramref name="block"/> afterward — the host owns the memory.
    /// </summary>
    public static void SetOutput(MemoryBlock block) => Imports.extism_output_set(block.Offset, block.Length);

    public static MemoryBlock Allocate(ulong size)
    {
        var offset = Imports.extism_alloc(size);
        return new MemoryBlock(offset, size);
    }

    public static MemoryBlock Allocate(ReadOnlySpan<byte> data)
    {
        var block = Allocate((ulong)data.Length);
        block.WriteBytes(data);
        return block;
    }

    public static MemoryBlock Allocate(string value) => Allocate(Encoding.UTF8.GetBytes(value).AsSpan());

    public static bool TryGetConfig(string key, out string? value)
    {
        using var keyBlock = Allocate(key);
        var resultOffset = Imports.extism_config_get(keyBlock.Offset);

        if (resultOffset == 0)
        {
            value = null;
            return false;
        }

        var len = Imports.extism_length(resultOffset);
        using var result = new MemoryBlock(resultOffset, len);
        value = result.ReadString();
        return true;
    }

    /// <summary>
    /// Gets a variable from the Extism host. The caller is responsible for disposing the returned <see cref="MemoryBlock"/>.
    /// </summary>
    public static bool TryGetVar(string key, out MemoryBlock value)
    {
        using var keyBlock = Allocate(key);
        var offset = Imports.extism_var_get(keyBlock.Offset);

        if (offset == 0)
        {
            value = default;
            return false;
        }

        var len = Imports.extism_length(offset);
        value = new MemoryBlock(offset, len);
        return true;
    }

    public static void SetVar(string key, ReadOnlySpan<byte> data)
    {
        using var keyBlock = Allocate(key);
        using var valBlock = Allocate(data);
        Imports.extism_var_set(keyBlock.Offset, valBlock.Offset);
    }

    public static void SetVar(string key, string value) => SetVar(key, Encoding.UTF8.GetBytes(value).AsSpan());

    public static void RemoveVar(string key)
    {
        using var keyBlock = Allocate(key);
        Imports.extism_var_set(keyBlock.Offset, 0);
    }

    public static void Log(LogLevel level, string message)
    {
        using var block = Allocate(message);
        switch (level)
        {
            case LogLevel.Trace: Imports.extism_log_trace(block.Offset); break;
            case LogLevel.Debug: Imports.extism_log_debug(block.Offset); break;
            case LogLevel.Info:  Imports.extism_log_info(block.Offset);  break;
            case LogLevel.Warn:  Imports.extism_log_warn(block.Offset);  break;
            case LogLevel.Error: Imports.extism_log_error(block.Offset); break;
        }
    }

    public static void SetError(string message)
    {
        // Do not free — extism_error_set references this memory until the host reads it.
        var block = Allocate(message);
        Imports.extism_error_set(block.Offset);
    }

    public static HttpResponse SendRequest(HttpRequest request)
    {
        // Extism expects the request metadata as a JSON object in memory.
        var json = BuildRequestJson(request);
        using var reqBlock = Allocate(json);

        ulong bodyOffset = 0;
        if (request.Body.Length > 0)
        {
            // Body block is freed by the host after the request completes.
            var bodyBlock = Allocate(request.Body.AsSpan());
            bodyOffset = bodyBlock.Offset;
        }

        var responseOffset = Imports.extism_http_request(reqBlock.Offset, bodyOffset);
        var statusCode = (ushort)Imports.extism_http_status_code();

        byte[] body;
        if (responseOffset != 0)
        {
            var responseLen = Imports.extism_length(responseOffset);
            using var responseBlock = new MemoryBlock(responseOffset, responseLen);
            body = responseBlock.ReadBytes();
        }
        else
        {
            body = [];
        }

        var headers = new Dictionary<string, string>();
        var headersOffset = Imports.extism_http_headers();
        if (headersOffset != 0)
        {
            var headersLen = Imports.extism_length(headersOffset);
            using var headersBlock = new MemoryBlock(headersOffset, headersLen);
            // Headers are returned as a JSON object; parse key-value pairs.
            ParseJsonHeaders(headersBlock.ReadString(), headers);
        }

        return new HttpResponse(statusCode, headers, body);
    }

    private static string BuildRequestJson(HttpRequest request)
    {
        var sb = new StringBuilder();
        sb.Append("{\"url\":\"");
        AppendJsonEscaped(sb, request.Url);
        sb.Append("\",\"method\":\"");
        AppendJsonEscaped(sb, request.Method);
        sb.Append('"');

        if (request.Headers.Count > 0)
        {
            sb.Append(",\"headers\":{");
            var first = true;
            foreach (var (k, v) in request.Headers)
            {
                if (!first) sb.Append(',');
                sb.Append('"');
                AppendJsonEscaped(sb, k);
                sb.Append("\":\"");
                AppendJsonEscaped(sb, v);
                sb.Append('"');
                first = false;
            }
            sb.Append('}');
        }

        sb.Append('}');
        return sb.ToString();
    }

    private static void AppendJsonEscaped(StringBuilder sb, string value)
    {
        foreach (var c in value)
        {
            switch (c)
            {
                case '"':  sb.Append("\\\""); break;
                case '\\': sb.Append("\\\\"); break;
                case '\n': sb.Append("\\n");  break;
                case '\r': sb.Append("\\r");  break;
                case '\t': sb.Append("\\t");  break;
                default:   sb.Append(c);      break;
            }
        }
    }

    private static void ParseJsonHeaders(string json, Dictionary<string, string> headers)
    {
        // Minimal JSON object parser for {"key":"value",...} returned by Extism.
        var span = json.AsSpan().Trim();
        if (span.Length < 2 || span[0] != '{') return;
        span = span[1..^1]; // strip { }

        while (span.Length > 0)
        {
            span = span.TrimStart();
            if (span.Length == 0 || span[0] != '"') break;
            var key = ReadJsonString(ref span);

            span = span.TrimStart();
            if (span.Length == 0 || span[0] != ':') break;
            span = span[1..].TrimStart();

            if (span.Length == 0 || span[0] != '"') break;
            var val = ReadJsonString(ref span);

            headers[key] = val;

            span = span.TrimStart();
            if (span.Length > 0 && span[0] == ',')
                span = span[1..];
        }
    }

    private static string ReadJsonString(ref ReadOnlySpan<char> span)
    {
        // span[0] == '"'
        span = span[1..];
        var sb = new StringBuilder();
        while (span.Length > 0)
        {
            var c = span[0];
            span = span[1..];
            if (c == '"') break;
            if (c == '\\' && span.Length > 0)
            {
                var next = span[0];
                span = span[1..];
                sb.Append(next switch
                {
                    'n' => '\n',
                    'r' => '\r',
                    't' => '\t',
                    _ => next,
                });
            }
            else
            {
                sb.Append(c);
            }
        }
        return sb.ToString();
    }
}
