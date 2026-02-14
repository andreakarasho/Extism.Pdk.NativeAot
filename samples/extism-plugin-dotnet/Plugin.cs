using Extism.Pdk;
using Google.FlatBuffers;
using ExamplePlugin;

public static class Plugin
{
    // String → String
    [ExtismExport("greet")]
    public static string Greet(string name) => $"Hello, {name}!";

    // No input → String
    [ExtismExport("version")]
    public static string Version() => "1.0.0";

    // String → Void (side-effect only)
    [ExtismExport("log")]
    public static void Log(string message)
    {
        Extism.Pdk.NativeAot.Pdk.Log(Extism.Pdk.NativeAot.LogLevel.Info, $"[Plugin] {message}");
    }

    // Two ints → int (binary packed params)
    [ExtismExport("add")]
    public static int Add(int a, int b) => a + b;

    // Three doubles → double
    [ExtismExport("weighted_avg")]
    public static double WeightedAvg(double value, double weight, double total) =>
        (value * weight) / total;

    // int → bool
    [ExtismExport("is_even")]
    public static bool IsEven(int n) => n % 2 == 0;

    // Two floats → float
    [ExtismExport("distance")]
    public static float Distance(float x, float y) =>
        (float)System.Math.Sqrt(x * x + y * y);

    // long → long
    [ExtismExport("factorial")]
    public static long Factorial(long n)
    {
        long result = 1;
        for (long i = 2; i <= n; i++)
            result *= i;
        return result;
    }

    // byte[] → byte[] (raw binary transform)
    [ExtismExport("xor_bytes")]
    public static byte[] XorBytes(byte[] data)
    {
        var result = new byte[data.Length];
        for (int i = 0; i < data.Length; i++)
            result[i] = (byte)(data[i] ^ 0xFF);
        return result;
    }

    // Mixed primitives: int, byte, bool → int
    [ExtismExport("cond_add")]
    public static int CondAdd(int value, byte amount, bool negate) =>
        negate ? value - amount : value + amount;

    // --- Complex type via FlatBuffers (auto-serialized) ---
    [ExtismExport("level_up")]
    public static PlayerData LevelUp(PlayerData player)
    {
        var name = player.Name;
        var score = player.Score + 100;
        var inventoryCount = player.InventoryLength;

        var builder = new FlatBufferBuilder(256);

        var nameOffset = builder.CreateString(name);

        var inventoryOffsets = new StringOffset[inventoryCount + 1];
        for (int i = 0; i < inventoryCount; i++)
            inventoryOffsets[i] = builder.CreateString(player.Inventory(i));
        inventoryOffsets[inventoryCount] = builder.CreateString("level-up-reward");

        var inventoryVector = PlayerData.CreateInventoryVector(builder, inventoryOffsets);

        var root = PlayerData.CreatePlayerData(builder, nameOffset, score, inventoryVector);
        builder.Finish(root.Value);

        return PlayerData.GetRootAsPlayerData(builder.DataBuffer);
    }
}
