namespace Extism.Pdk.NativeAot;

public sealed class HttpResponse
{
    public ushort StatusCode { get; }
    public Dictionary<string, string> Headers { get; }
    public byte[] Body { get; }

    internal HttpResponse(ushort statusCode, Dictionary<string, string> headers, byte[] body)
    {
        StatusCode = statusCode;
        Headers = headers;
        Body = body;
    }
}
