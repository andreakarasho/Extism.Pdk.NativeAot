using System;
using System.Collections.Generic;
using System.Collections.Immutable;
using System.Linq;
using System.Text;
using System.Threading;
using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp.Syntax;

namespace Extism.Pdk.SourceGenerator;

internal enum TypeCategory
{
    Other,
    Void,
    String,
    ByteArray,
    Bool,
    Int8,
    UInt8,
    Int16,
    UInt16,
    Int32,
    UInt32,
    Int64,
    UInt64,
    Float32,
    Float64,
    FlatBuffer,
}

internal readonly struct LocationInfo : IEquatable<LocationInfo>
{
    public readonly string FilePath;
    public readonly int StartLine;
    public readonly int StartColumn;
    public readonly int EndLine;
    public readonly int EndColumn;

    public LocationInfo(string filePath, int startLine, int startColumn, int endLine, int endColumn)
    {
        FilePath = filePath ?? "";
        StartLine = startLine;
        StartColumn = startColumn;
        EndLine = endLine;
        EndColumn = endColumn;
    }

    public Location ToLocation() => Location.Create(
        FilePath,
        Microsoft.CodeAnalysis.Text.TextSpan.FromBounds(0, 0),
        new Microsoft.CodeAnalysis.Text.LinePositionSpan(
            new Microsoft.CodeAnalysis.Text.LinePosition(StartLine, StartColumn),
            new Microsoft.CodeAnalysis.Text.LinePosition(EndLine, EndColumn)));

    public bool Equals(LocationInfo other) =>
        FilePath == other.FilePath &&
        StartLine == other.StartLine &&
        StartColumn == other.StartColumn &&
        EndLine == other.EndLine &&
        EndColumn == other.EndColumn;

    public override bool Equals(object? obj) => obj is LocationInfo other && Equals(other);

    public override int GetHashCode()
    {
        unchecked
        {
            int hash = 17;
            hash = hash * 31 + (FilePath?.GetHashCode() ?? 0);
            hash = hash * 31 + StartLine;
            hash = hash * 31 + StartColumn;
            return hash;
        }
    }
}

internal readonly struct ExportParameterInfo : IEquatable<ExportParameterInfo>
{
    public readonly string Name;
    public readonly string TypeFullName;
    public readonly TypeCategory Category;

    public ExportParameterInfo(string name, string typeFullName, TypeCategory category)
    {
        Name = name;
        TypeFullName = typeFullName;
        Category = category;
    }

    public bool Equals(ExportParameterInfo other) =>
        Name == other.Name &&
        TypeFullName == other.TypeFullName &&
        Category == other.Category;

    public override bool Equals(object? obj) => obj is ExportParameterInfo other && Equals(other);

    public override int GetHashCode()
    {
        unchecked
        {
            int hash = 17;
            hash = hash * 31 + (Name?.GetHashCode() ?? 0);
            hash = hash * 31 + (TypeFullName?.GetHashCode() ?? 0);
            hash = hash * 31 + (int)Category;
            return hash;
        }
    }
}

internal readonly struct ExportInfo : IEquatable<ExportInfo>
{
    public readonly string ExportName;
    public readonly string ContainingTypeFullName;
    public readonly string MethodName;
    public readonly ImmutableArray<ExportParameterInfo> Parameters;
    public readonly string ReturnTypeFullName;
    public readonly TypeCategory ReturnCategory;
    public readonly bool IsStatic;
    public readonly bool IsGeneric;
    public readonly int Accessibility; // Roslyn Accessibility enum cast to int
    public readonly LocationInfo Location;

    public ExportInfo(
        string exportName,
        string containingTypeFullName,
        string methodName,
        ImmutableArray<ExportParameterInfo> parameters,
        string returnTypeFullName,
        TypeCategory returnCategory,
        bool isStatic,
        bool isGeneric,
        int accessibility,
        LocationInfo location)
    {
        ExportName = exportName;
        ContainingTypeFullName = containingTypeFullName;
        MethodName = methodName;
        Parameters = parameters;
        ReturnTypeFullName = returnTypeFullName;
        ReturnCategory = returnCategory;
        IsStatic = isStatic;
        IsGeneric = isGeneric;
        Accessibility = accessibility;
        Location = location;
    }

    public bool Equals(ExportInfo other)
    {
        if (ExportName != other.ExportName ||
            ContainingTypeFullName != other.ContainingTypeFullName ||
            MethodName != other.MethodName ||
            ReturnTypeFullName != other.ReturnTypeFullName ||
            ReturnCategory != other.ReturnCategory ||
            IsStatic != other.IsStatic ||
            IsGeneric != other.IsGeneric ||
            Accessibility != other.Accessibility ||
            !Location.Equals(other.Location))
            return false;

        if (Parameters.Length != other.Parameters.Length) return false;
        for (int i = 0; i < Parameters.Length; i++)
        {
            if (!Parameters[i].Equals(other.Parameters[i])) return false;
        }
        return true;
    }

    public override bool Equals(object? obj) => obj is ExportInfo other && Equals(other);

    public override int GetHashCode()
    {
        unchecked
        {
            int hash = 17;
            hash = hash * 31 + (ExportName?.GetHashCode() ?? 0);
            hash = hash * 31 + (ContainingTypeFullName?.GetHashCode() ?? 0);
            hash = hash * 31 + (MethodName?.GetHashCode() ?? 0);
            hash = hash * 31 + Parameters.Length;
            hash = hash * 31 + (ReturnTypeFullName?.GetHashCode() ?? 0);
            hash = hash * 31 + (int)ReturnCategory;
            return hash;
        }
    }
}

internal static class Diagnostics
{
    public static readonly DiagnosticDescriptor MethodMustBeStatic = new(
        "EXTISM001",
        "ExtismExport method must be static",
        "Method '{0}' must be static to be an Extism export",
        "Extism",
        DiagnosticSeverity.Error,
        true);

    public static readonly DiagnosticDescriptor MethodCannotBeGeneric = new(
        "EXTISM002",
        "ExtismExport method cannot be generic",
        "Method '{0}' cannot be generic to be an Extism export",
        "Extism",
        DiagnosticSeverity.Error,
        true);

    public static readonly DiagnosticDescriptor MethodMustBeAccessible = new(
        "EXTISM003",
        "ExtismExport method must be accessible",
        "Method '{0}' must be public or internal to be an Extism export",
        "Extism",
        DiagnosticSeverity.Error,
        true);

    public static readonly DiagnosticDescriptor DuplicateExportName = new(
        "EXTISM004",
        "Duplicate ExtismExport name",
        "Export name '{0}' is used by multiple methods",
        "Extism",
        DiagnosticSeverity.Error,
        true);

    public static readonly DiagnosticDescriptor UnsupportedParameterType = new(
        "EXTISM005",
        "Unsupported ExtismExport parameter type",
        "Parameter '{0}' has unsupported type '{1}'. Supported: string, byte[], bool, sbyte, byte, short, ushort, int, uint, long, ulong, float, double, IFlatbufferObject types. Multi-parameter exports support primitives only.",
        "Extism",
        DiagnosticSeverity.Error,
        true);

    public static readonly DiagnosticDescriptor UnsupportedReturnType = new(
        "EXTISM006",
        "Unsupported ExtismExport return type",
        "Return type '{0}' is not supported. Supported: void, string, byte[], bool, sbyte, byte, short, ushort, int, uint, long, ulong, float, double, IFlatbufferObject types",
        "Extism",
        DiagnosticSeverity.Error,
        true);
}

[Generator]
public class ExtismExportGenerator : IIncrementalGenerator
{
    private const string AttributeSource = @"// <auto-generated/>
#nullable enable

namespace Extism.Pdk
{
    [global::System.AttributeUsage(global::System.AttributeTargets.Method)]
    public sealed class ExtismExportAttribute : global::System.Attribute
    {
        public string Name { get; }
        public ExtismExportAttribute(string name) => Name = name;
    }
}
";

    public void Initialize(IncrementalGeneratorInitializationContext context)
    {
        context.RegisterPostInitializationOutput(static ctx =>
            ctx.AddSource("ExtismExportAttribute.g.cs", AttributeSource));

        var exports = context.SyntaxProvider
            .ForAttributeWithMetadataName(
                "Extism.Pdk.ExtismExportAttribute",
                predicate: static (node, _) => node is MethodDeclarationSyntax,
                transform: static (ctx, ct) => GetExportInfo(ctx, ct));

        context.RegisterSourceOutput(
            exports.Collect(),
            static (spc, exports) => Execute(spc, exports));
    }

    private static ExportInfo GetExportInfo(
        GeneratorAttributeSyntaxContext ctx,
        CancellationToken ct)
    {
        var method = (IMethodSymbol)ctx.TargetSymbol;
        var attr = ctx.Attributes[0];
        var exportName = (string)attr.ConstructorArguments[0].Value!;

        var containingType = method.ContainingType.ToDisplayString(
            SymbolDisplayFormat.FullyQualifiedFormat);

        var parameters = ImmutableArray.CreateBuilder<ExportParameterInfo>(method.Parameters.Length);
        foreach (var p in method.Parameters)
        {
            parameters.Add(new ExportParameterInfo(
                p.Name,
                p.Type.ToDisplayString(SymbolDisplayFormat.FullyQualifiedFormat),
                GetTypeCategory(p.Type)));
        }

        var location = ctx.TargetNode.GetLocation();
        var lineSpan = location.GetMappedLineSpan();

        return new ExportInfo(
            exportName,
            containingType,
            method.Name,
            parameters.ToImmutable(),
            method.ReturnType.ToDisplayString(SymbolDisplayFormat.FullyQualifiedFormat),
            GetTypeCategory(method.ReturnType),
            method.IsStatic,
            method.IsGenericMethod,
            (int)method.DeclaredAccessibility,
            new LocationInfo(
                lineSpan.Path,
                lineSpan.StartLinePosition.Line,
                lineSpan.StartLinePosition.Character,
                lineSpan.EndLinePosition.Line,
                lineSpan.EndLinePosition.Character));
    }

    private static TypeCategory GetTypeCategory(ITypeSymbol type)
    {
        if (type is IArrayTypeSymbol arrayType && arrayType.ElementType.SpecialType == SpecialType.System_Byte)
            return TypeCategory.ByteArray;

        var result = type.SpecialType switch
        {
            SpecialType.System_Void => TypeCategory.Void,
            SpecialType.System_String => TypeCategory.String,
            SpecialType.System_Boolean => TypeCategory.Bool,
            SpecialType.System_SByte => TypeCategory.Int8,
            SpecialType.System_Byte => TypeCategory.UInt8,
            SpecialType.System_Int16 => TypeCategory.Int16,
            SpecialType.System_UInt16 => TypeCategory.UInt16,
            SpecialType.System_Int32 => TypeCategory.Int32,
            SpecialType.System_UInt32 => TypeCategory.UInt32,
            SpecialType.System_Int64 => TypeCategory.Int64,
            SpecialType.System_UInt64 => TypeCategory.UInt64,
            SpecialType.System_Single => TypeCategory.Float32,
            SpecialType.System_Double => TypeCategory.Float64,
            _ => TypeCategory.Other,
        };

        if (result == TypeCategory.Other)
        {
            foreach (var iface in type.AllInterfaces)
            {
                if (iface.Name == "IFlatbufferObject" &&
                    iface.ContainingNamespace?.ToDisplayString() == "Google.FlatBuffers")
                {
                    return TypeCategory.FlatBuffer;
                }
            }
        }

        return result;
    }

    private static bool IsPrimitive(TypeCategory cat) =>
        cat >= TypeCategory.Bool && cat <= TypeCategory.Float64;

    private static int GetPrimitiveSize(TypeCategory cat) => cat switch
    {
        TypeCategory.Bool or TypeCategory.Int8 or TypeCategory.UInt8 => 1,
        TypeCategory.Int16 or TypeCategory.UInt16 => 2,
        TypeCategory.Int32 or TypeCategory.UInt32 or TypeCategory.Float32 => 4,
        TypeCategory.Int64 or TypeCategory.UInt64 or TypeCategory.Float64 => 8,
        _ => -1,
    };

    private static void Execute(
        SourceProductionContext context,
        ImmutableArray<ExportInfo> exports)
    {
        if (exports.IsDefaultOrEmpty) return;

        var sb = new StringBuilder();
        sb.AppendLine("// <auto-generated/>");
        sb.AppendLine("using System.Buffers.Binary;");
        sb.AppendLine("using System.Runtime.InteropServices;");
        sb.AppendLine("using Extism.Pdk.NativeAot;");
        sb.AppendLine();
        sb.AppendLine("internal static class __ExtismExports");
        sb.AppendLine("{");

        bool hasFlatBufferInput = false;
        foreach (var e in exports)
        {
            if (e.Parameters.Length == 1 && e.Parameters[0].Category == TypeCategory.FlatBuffer)
            {
                hasFlatBufferInput = true;
                break;
            }
        }
        if (hasFlatBufferInput)
        {
            sb.AppendLine("    private static byte[] __inputBBArray;");
            sb.AppendLine("    private static global::Google.FlatBuffers.ByteBuffer __inputBB;");
            sb.AppendLine();
        }

        var seenNames = new HashSet<string>(StringComparer.Ordinal);
        bool any = false;

        foreach (var export in exports)
        {
            if (!export.IsStatic)
            {
                context.ReportDiagnostic(Diagnostic.Create(
                    Diagnostics.MethodMustBeStatic,
                    export.Location.ToLocation(),
                    export.MethodName));
                continue;
            }

            if (export.IsGeneric)
            {
                context.ReportDiagnostic(Diagnostic.Create(
                    Diagnostics.MethodCannotBeGeneric,
                    export.Location.ToLocation(),
                    export.MethodName));
                continue;
            }

            // Internal = 4, ProtectedOrInternal = 5, Public = 6
            if (export.Accessibility < 4)
            {
                context.ReportDiagnostic(Diagnostic.Create(
                    Diagnostics.MethodMustBeAccessible,
                    export.Location.ToLocation(),
                    export.MethodName));
                continue;
            }

            if (!seenNames.Add(export.ExportName))
            {
                context.ReportDiagnostic(Diagnostic.Create(
                    Diagnostics.DuplicateExportName,
                    export.Location.ToLocation(),
                    export.ExportName));
                continue;
            }

            // Validate return type
            if (export.ReturnCategory == TypeCategory.Other)
            {
                context.ReportDiagnostic(Diagnostic.Create(
                    Diagnostics.UnsupportedReturnType,
                    export.Location.ToLocation(),
                    export.ReturnTypeFullName));
                continue;
            }

            // Validate parameter types
            bool paramsValid = true;
            if (export.Parameters.Length == 1)
            {
                var p = export.Parameters[0];
                if (p.Category == TypeCategory.Other)
                {
                    context.ReportDiagnostic(Diagnostic.Create(
                        Diagnostics.UnsupportedParameterType,
                        export.Location.ToLocation(),
                        p.Name, p.TypeFullName));
                    paramsValid = false;
                }
            }
            else if (export.Parameters.Length > 1)
            {
                // Multi-param: all must be fixed-size primitives
                foreach (var p in export.Parameters)
                {
                    if (!IsPrimitive(p.Category))
                    {
                        context.ReportDiagnostic(Diagnostic.Create(
                            Diagnostics.UnsupportedParameterType,
                            export.Location.ToLocation(),
                            p.Name, p.TypeFullName));
                        paramsValid = false;
                        break;
                    }
                }
            }
            if (!paramsValid) continue;

            if (any) sb.AppendLine();
            GenerateWrapper(sb, export);
            any = true;
        }

        sb.AppendLine("}");
        context.AddSource("__ExtismExports.g.cs", sb.ToString());
    }

    private static void GenerateWrapper(StringBuilder sb, ExportInfo export)
    {
        var safeName = SanitizeIdentifier(export.ExportName);
        var entryPoint = export.ExportName.Replace('_', '-');
        sb.AppendLine($"    [UnmanagedCallersOnly(EntryPoint = \"{entryPoint}\")]");
        sb.AppendLine($"    public static uint __export_{safeName}()");
        sb.AppendLine("    {");
        sb.AppendLine("        try");
        sb.AppendLine("        {");

        GenerateInputDeserialization(sb, export);

        var args = GenerateArguments(export);
        bool isVoid = export.ReturnCategory == TypeCategory.Void;

        if (isVoid)
        {
            sb.AppendLine($"            {export.ContainingTypeFullName}.{export.MethodName}({args});");
        }
        else
        {
            sb.AppendLine($"            var __result = {export.ContainingTypeFullName}.{export.MethodName}({args});");
            GenerateOutputSerialization(sb, export);
        }

        sb.AppendLine("            return 0;");
        sb.AppendLine("        }");
        sb.AppendLine("        catch (global::System.Exception __ex)");
        sb.AppendLine("        {");
        sb.AppendLine("            Pdk.SetError(__ex.Message);");
        sb.AppendLine("            return 1;");
        sb.AppendLine("        }");
        sb.AppendLine("    }");
    }

    private static void GenerateInputDeserialization(StringBuilder sb, ExportInfo export)
    {
        if (export.Parameters.Length == 0)
            return;

        if (export.Parameters.Length == 1)
        {
            var p = export.Parameters[0];
            switch (p.Category)
            {
                case TypeCategory.String:
                    sb.AppendLine($"            var @{p.Name} = Pdk.GetInputString();");
                    return;
                case TypeCategory.ByteArray:
                    sb.AppendLine($"            var @{p.Name} = Pdk.GetInputSpan().ToArray();");
                    return;
                case TypeCategory.FlatBuffer:
                    var simpleName = GetSimpleTypeName(p.TypeFullName);
                    // Cached ByteBuffer — zero alloc in steady state; ByteBuffer tolerates trailing bytes
                    sb.AppendLine("            Pdk.GetInputSpan();");
                    sb.AppendLine("            if (__inputBBArray != Pdk.InputBufferArray)");
                    sb.AppendLine("            {");
                    sb.AppendLine("                __inputBBArray = Pdk.InputBufferArray;");
                    sb.AppendLine("                __inputBB = new global::Google.FlatBuffers.ByteBuffer(__inputBBArray);");
                    sb.AppendLine("            }");
                    sb.AppendLine("            else { __inputBB.Reset(); }");
                    sb.AppendLine($"            var @{p.Name} = {p.TypeFullName}.GetRootAs{simpleName}(__inputBB);");
                    return;
                default:
                {
                    // Single primitive — stackalloc
                    int size = GetPrimitiveSize(p.Category);
                    sb.AppendLine($"            System.Span<byte> __input = stackalloc byte[{size}];");
                    sb.AppendLine("            Pdk.LoadInputInto(__input);");
                    sb.AppendLine($"            var @{p.Name} = {GetPrimitiveReadExpr(p.Category, "__input", 0)};");
                    return;
                }
            }
        }
        else
        {
            // Multiple params - all primitives, compute total size for stackalloc
            int totalSize = 0;
            foreach (var p in export.Parameters)
                totalSize += GetPrimitiveSize(p.Category);

            sb.AppendLine($"            System.Span<byte> __input = stackalloc byte[{totalSize}];");
            sb.AppendLine("            Pdk.LoadInputInto(__input);");
            int offset = 0;
            foreach (var p in export.Parameters)
            {
                sb.AppendLine($"            var @{p.Name} = {GetPrimitiveReadExpr(p.Category, "__input", offset)};");
                offset += GetPrimitiveSize(p.Category);
            }
        }

    }

    private static string GenerateArguments(ExportInfo export)
    {
        if (export.Parameters.Length == 0)
            return "";

        return string.Join(", ", export.Parameters.Select(p => $"@{p.Name}"));
    }

    private static void GenerateOutputSerialization(StringBuilder sb, ExportInfo export)
    {
        switch (export.ReturnCategory)
        {
            case TypeCategory.String:
            case TypeCategory.ByteArray:
                sb.AppendLine("            Pdk.SetOutput(__result);");
                break;
            case TypeCategory.FlatBuffer:
                sb.AppendLine("            Pdk.SetOutput(__result.ByteBuffer.ToSizedReadOnlySpan());");
                break;
            case TypeCategory.Bool:
                sb.AppendLine("            System.Span<byte> __output = stackalloc byte[1];");
                sb.AppendLine("            __output[0] = __result ? (byte)1 : (byte)0;");
                sb.AppendLine("            Pdk.SetOutput(__output);");
                break;
            case TypeCategory.Int8:
                sb.AppendLine("            System.Span<byte> __output = stackalloc byte[1];");
                sb.AppendLine("            __output[0] = (byte)__result;");
                sb.AppendLine("            Pdk.SetOutput(__output);");
                break;
            case TypeCategory.UInt8:
                sb.AppendLine("            System.Span<byte> __output = stackalloc byte[1];");
                sb.AppendLine("            __output[0] = __result;");
                sb.AppendLine("            Pdk.SetOutput(__output);");
                break;
            default:
            {
                int size = GetPrimitiveSize(export.ReturnCategory);
                string writeCall = GetPrimitiveWriteCall(export.ReturnCategory, "__output");
                sb.AppendLine($"            System.Span<byte> __output = stackalloc byte[{size}];");
                sb.AppendLine($"            {writeCall};");
                sb.AppendLine("            Pdk.SetOutput(__output);");
                break;
            }
        }
    }

    private static string GetPrimitiveReadExpr(TypeCategory cat, string arrayVar, int offset)
    {
        // Byte-sized types: direct array indexing
        if (cat == TypeCategory.Bool)
            return $"({arrayVar}[{offset}] != 0)";
        if (cat == TypeCategory.Int8)
            return $"(sbyte){arrayVar}[{offset}]";
        if (cat == TypeCategory.UInt8)
            return $"{arrayVar}[{offset}]";

        // Multi-byte types: BinaryPrimitives
        var spanExpr = offset == 0 ? arrayVar : $"{arrayVar}.Slice({offset})";
        return cat switch
        {
            TypeCategory.Int16 => $"BinaryPrimitives.ReadInt16LittleEndian({spanExpr})",
            TypeCategory.UInt16 => $"BinaryPrimitives.ReadUInt16LittleEndian({spanExpr})",
            TypeCategory.Int32 => $"BinaryPrimitives.ReadInt32LittleEndian({spanExpr})",
            TypeCategory.UInt32 => $"BinaryPrimitives.ReadUInt32LittleEndian({spanExpr})",
            TypeCategory.Int64 => $"BinaryPrimitives.ReadInt64LittleEndian({spanExpr})",
            TypeCategory.UInt64 => $"BinaryPrimitives.ReadUInt64LittleEndian({spanExpr})",
            TypeCategory.Float32 => $"BinaryPrimitives.ReadSingleLittleEndian({spanExpr})",
            TypeCategory.Float64 => $"BinaryPrimitives.ReadDoubleLittleEndian({spanExpr})",
            _ => throw new ArgumentException($"Not a primitive: {cat}"),
        };
    }

    private static string GetPrimitiveWriteCall(TypeCategory cat, string arrayVar)
    {
        return cat switch
        {
            TypeCategory.Int16 => $"BinaryPrimitives.WriteInt16LittleEndian({arrayVar}, __result)",
            TypeCategory.UInt16 => $"BinaryPrimitives.WriteUInt16LittleEndian({arrayVar}, __result)",
            TypeCategory.Int32 => $"BinaryPrimitives.WriteInt32LittleEndian({arrayVar}, __result)",
            TypeCategory.UInt32 => $"BinaryPrimitives.WriteUInt32LittleEndian({arrayVar}, __result)",
            TypeCategory.Int64 => $"BinaryPrimitives.WriteInt64LittleEndian({arrayVar}, __result)",
            TypeCategory.UInt64 => $"BinaryPrimitives.WriteUInt64LittleEndian({arrayVar}, __result)",
            TypeCategory.Float32 => $"BinaryPrimitives.WriteSingleLittleEndian({arrayVar}, __result)",
            TypeCategory.Float64 => $"BinaryPrimitives.WriteDoubleLittleEndian({arrayVar}, __result)",
            _ => throw new ArgumentException($"Not a multi-byte primitive: {cat}"),
        };
    }

    private static string SanitizeIdentifier(string name)
    {
        var sb = new StringBuilder(name.Length);
        foreach (var c in name)
        {
            sb.Append(char.IsLetterOrDigit(c) ? c : '_');
        }
        return sb.ToString();
    }

    private static string GetSimpleTypeName(string fullyQualifiedName)
    {
        var idx = fullyQualifiedName.LastIndexOf('.');
        return idx >= 0 ? fullyQualifiedName.Substring(idx + 1) : fullyQualifiedName;
    }
}
