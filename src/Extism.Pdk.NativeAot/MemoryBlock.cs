using System.Text;

namespace Extism.Pdk.NativeAot;

/// <summary>
/// Wraps an offset + length in Extism linear memory.
/// Dispose to free the underlying allocation via <c>extism_free</c>.
/// Do NOT dispose blocks passed to <see cref="Pdk.SetOutput(MemoryBlock)"/> — the host owns that memory.
/// </summary>
public struct MemoryBlock : IDisposable
{
    public ulong Offset { get; private set; }
    public ulong Length { get; }

    internal MemoryBlock(ulong offset, ulong length)
    {
        Offset = offset;
        Length = length;
    }

    public byte[] ReadBytes()
    {
        var buf = new byte[Length];
        ReadBytesInto(buf);
        return buf;
    }

    public void ReadBytesInto(Span<byte> buffer)
    {
        var chunks = Length / 8;

        for (ulong i = 0; i < chunks; i++)
        {
            var val = Imports.extism_load_u64(Offset + i * 8);
            BitConverter.TryWriteBytes(buffer.Slice((int)(i * 8)), val);
        }

        for (var i = chunks * 8; i < Length; i++)
        {
            buffer[(int)i] = Imports.extism_load_u8(Offset + i);
        }
    }

    public string ReadString() => Encoding.UTF8.GetString(ReadBytes());

    public void WriteBytes(ReadOnlySpan<byte> data)
    {
        var len = (ulong)data.Length;
        var chunks = len / 8;

        for (ulong i = 0; i < chunks; i++)
        {
            var val = BitConverter.ToUInt64(data.Slice((int)(i * 8)));
            Imports.extism_store_u64(Offset + i * 8, val);
        }

        for (var i = chunks * 8; i < len; i++)
        {
            Imports.extism_store_u8(Offset + i, data[(int)i]);
        }
    }

    public void WriteString(string value) => WriteBytes(Encoding.UTF8.GetBytes(value));

    public void Dispose()
    {
        if (Offset != 0)
        {
            Imports.extism_free(Offset);
            Offset = 0;
        }
    }
}
