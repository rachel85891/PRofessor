using Microsoft.Data.SqlClient;

var builder = WebApplication.CreateBuilder(args);
var app = builder.Build();

var connectionString = app.Configuration.GetConnectionString("Main");

app.MapGet("/api/dbname", async () =>
{
    await using var connection = new SqlConnection(connectionString);
    await connection.OpenAsync();
    await using var command = new SqlCommand("SELECT DB_NAME()", connection);
    return (string?)await command.ExecuteScalarAsync();
});

app.Run();
