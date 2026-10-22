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
    if (request.Action is not ("catalog" or "object") || string.IsNullOrWhiteSpace(request.Server)
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
        // Reject ordinary database tables: GetMetadataObject alone also accepts SQL table names.
        if (string.IsNullOrWhiteSpace(request.Name) || request.Name.Split('.').Length != 2
            || provider.GetMetadataEntry(request.Name) is null) throw new ArgumentException();
        var entity = provider.GetMetadataObject(request.Name) ?? throw new InvalidOperationException();
        var properties = entity.Properties.Select(property => new
        {
            name = property.Name, type = property.Type.ToString(),
            references = provider.ResolveReferences(property.References),
            columns = property.Columns.Select(column => new {
                name = column.Name, purpose = column.Purpose.ToString(), type = column.Type.ToString(),
                row_version = column.IsGenerated && column.Name == "_Version",
                is_date = column.Type.IsDateTime && !column.Type.IsUnion
            }).ToArray()
        }).ToArray();
        Console.WriteLine(JsonSerializer.Serialize(new { protocol = 1, ok = true,
            configuration = config.Name, version = config.AppConfigVersion, year_offset = config.YearOffset,
            name = request.Name, table = entity.DbName, properties,
            table_parts = entity.Entities.Select(part => new { name = part.Name, table = part.DbName }).ToArray() }));
    }
    return 0;
}
catch (Exception)
{
    // Never print exception details or the connection string: both may contain secrets.
    Console.WriteLine("{\"protocol\":1,\"ok\":false,\"code\":\"metadata_read_failed\"}");
    return 1;
}

sealed class Request
{
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
}
