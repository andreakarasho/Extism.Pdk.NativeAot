namespace Extism.Pdk.NativeAot;

public sealed class HttpRequest
{
    public string Url { get; set; } = string.Empty;
    public string Method { get; set; } = "GET";
    public Dictionary<string, string> Headers { get; set; } = new();
    public byte[] Body { get; set; } = [];
}
