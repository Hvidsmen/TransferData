using System.Text;
using System.Text.Json;
using DaJet.Data;
using DaJet.Metadata;
using DaJet.TypeSystem;
using Microsoft.Data.SqlClient;

Console.InputEncoding = new UTF8Encoding(false);
Console.OutputEncoding = new UTF8Encoding(false);
if (args.Length == 1 && args[0] == "--version")
{
    Console.WriteLine("{\"protocol\":1,\"reader\":\"DaJet.Metadata\",\"version\":\"5.1.5\"}");
    return 0;
}
try
{
    var request = JsonSerializer.Deserialize<Request>(Console.In.ReadToEnd(),
        new JsonSerializerOptions { PropertyNameCaseInsensitive = true }) ?? throw new ArgumentException();
    if (request.Action is not ("catalog" or "object" or "objects") || string.IsNullOrWhiteSpace(request.Server)
        || string.IsNullOrWhiteSpace(request.Database) || request.Port is < 1 or > 65535
        || request.Timeout is < 1 or > 30 || request.Authentication is not ("WINDOWS" or "SQL"))
        throw new ArgumentException();
    var builder = new SqlConnectionStringBuilder
    {
        DataSource = $"{request.Server},{request.Port}", InitialCatalog = request.Database,
        IntegratedSecurity = request.Authentication == "WINDOWS", ConnectTimeout = request.Timeout,
        TrustServerCertificate = request.TrustCertificate, ApplicationName = "ETL Dev 1C Metadata Reader",
        ApplicationIntent = ApplicationIntent.ReadOnly
    };
    builder["Encrypt"] = "True";
    if (!builder.IntegratedSecurity)
    {
        builder.UserID = request.Username;
        builder.Password = request.Password;
    }
    string connectionString = builder.ConnectionString;
    var provider = MetadataProvider.Create(DataSourceType.SqlServer, in connectionString);
    var config = provider.GetConfiguration();
    if (request.Action == "catalog")
    {
        var objects = MetadataNames.GetApplicationObjectNames()
            .SelectMany(type => provider.GetMetadataNames(type).Select(name => new { name = type + "." + name, kind = type }))
            .OrderBy(item => item.name, StringComparer.Ordinal).ToArray();
        Console.WriteLine(JsonSerializer.Serialize(new { protocol = 1, ok = true,
            configuration = config.Name, version = config.AppConfigVersion, year_offset = config.YearOffset, objects }));
    }
    else
    {
        object ReadObject(string objectName)
        {
            // Reject ordinary database tables: GetMetadataObject alone also accepts SQL table names.
            if (string.IsNullOrWhiteSpace(objectName) || objectName.Split('.').Length != 2
                || provider.GetMetadataEntry(objectName) is null) throw new ArgumentException();
            var entity = provider.GetMetadataObject(objectName) ?? throw new InvalidOperationException();
            var targets = new Dictionary<string, object>();
            string[] References(PropertyDefinition property)
            {
                if (!property.Type.IsEntity || property.Name == "Ссылка") return Array.Empty<string>();
                var names = provider.ResolveReferences(property.References);
                if (property.Type.TypeCode > 0)
                {
                    var entry = provider.GetMetadataEntry(property.Type.TypeCode);
                    if (entry is not null) names = new List<string> { entry.FullName };
                }
                foreach (string name in names.Distinct())
                {
                    if (targets.ContainsKey(name)) continue;
                    var entry = provider.GetMetadataEntry(name);
                    if (entry is null || entry.Type is not ("Справочник" or "Документ" or "Перечисление")) continue;
                    // Enumeration entities expose values, not physical reference columns.
                    // Resolve them directly from configuration; no table join is needed.
                    if (entry.Type == "Перечисление")
                    {
                        targets[name] = new { name, kind = entry.Type, type_code = entry.Code,
                            values = provider.GetEnumerationValues(name).Select(value => new {
                                name = value.Key, hex = Convert.ToHexString(UuidConverter.GetDbUuid(value.Value))
                            }).ToArray() };
                        continue;
                    }
                    var target = provider.GetMetadataObject(name);
                    string? Field(string field) => target.GetPropertyByName(field)?.Columns.SingleOrDefault()?.Name;
                    string? id = Field("Ссылка");
                    if (id is null) continue;
                    targets[name] = new { name, kind = entry.Type, type_code = entry.Code,
                        table = target.DbName, id, description = Field("Наименование"), date = Field("Дата"),
                        number = Field("Номер"), partitions = Partitions(target) };
                }
                return names.Distinct().ToArray();
            }
            object ReadProperty(PropertyDefinition property) => new
            {
                name = property.Name, type = property.Type.ToString(),
                is_measure = property.Purpose.IsMeasure(), is_dimension = property.Purpose.IsDimension(),
                references = References(property), is_reference = property.Type.IsEntity && property.Name != "Ссылка",
                columns = property.Columns.Select(column => new {
                    name = column.Name, purpose = column.Purpose.ToString(), type = column.Type.ToString(),
                    row_version = column.IsGenerated && column.Name == "_Version",
                    is_date = column.Type.IsDateTime && !column.Type.IsUnion
                }).ToArray()
            };
            var properties = entity.Properties.Select(ReadProperty).ToArray();
            var parts = entity.Entities.Select(part => {
                if (!request.IncludeTableParts) return (object)new { name = part.Name, table = part.DbName };
                return (object)new { name = part.Name, table = part.DbName,
                    properties = part.Properties.Select(ReadProperty).ToArray(), partitions = Partitions(part) };
            }).ToArray();
            return new { protocol = 1, ok = true,
                configuration = config.Name, version = config.AppConfigVersion, year_offset = config.YearOffset,
                name = objectName, table = entity.DbName, properties,
                reference_version = 1, reference_targets = targets.Values.ToArray(), partitions = Partitions(entity),
                table_parts = parts };
        }
        if (request.Action == "objects")
        {
            if (request.Names is null || request.Names.Length is < 1 or > 64) throw new ArgumentException();
            var objects = request.Names.Distinct().Select(ReadObject).ToArray();
            Console.WriteLine(JsonSerializer.Serialize(new { protocol = 1, ok = true, objects }));
        }
        else Console.WriteLine(JsonSerializer.Serialize(ReadObject(request.Name)));
    }

    return 0;
}
catch (Exception)
{
    // Never print exception details or the connection string: both may contain secrets.
    Console.WriteLine("{\"protocol\":1,\"ok\":false,\"code\":\"metadata_read_failed\"}");
    return 1;
}

static object[] Partitions(EntityDefinition entity) => entity.Properties
    .Where(property => property.Purpose.UseDataSeparation())
    .SelectMany(property => property.Columns.Select(column => (object)new {
        key = property.Uuid.ToString() + ":" + column.Purpose, source = column.Name
    })).ToArray();

sealed class Request
{
    public bool IncludeTableParts { get; set; }
    public string Action { get; set; } = "catalog";
    public string Server { get; set; } = "";
    public int Port { get; set; } = 1433;
    public string Database { get; set; } = "";
    public string Authentication { get; set; } = "WINDOWS";
    public string Username { get; set; } = "";
    public string Password { get; set; } = "";
    public bool TrustCertificate { get; set; }
    public int Timeout { get; set; } = 10;
    public string Name { get; set; } = "";
    public string[] Names { get; set; } = Array.Empty<string>();
}
