using Microsoft.Data.SqlClient;

var builder = WebApplication.CreateBuilder(args);
var app = builder.Build();

app.MapGet("/api/dbname", async () =>
{
    await using var connection = new SqlConnection(
        "Server=.\\SQLEXPRESS;Database=MadeUpShop;Trusted_Connection=True;TrustServerCertificate=True;");
    await connection.OpenAsync();
    await using var command = new SqlCommand("SELECT DB_NAME()", connection);
    return (string?)await command.ExecuteScalarAsync();
});

app.Run();
