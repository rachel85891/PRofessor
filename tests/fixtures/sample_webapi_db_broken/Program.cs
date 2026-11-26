using Microsoft.EntityFrameworkCore;

var builder = WebApplication.CreateBuilder(args);
builder.Services.AddDbContext<BrokenDb>(options =>
    options.UseSqlServer(builder.Configuration.GetConnectionString("Main")));
var app = builder.Build();

app.MapGet("/api/ping", () => "pong");

app.Run();

// Neither entity has a key, so EF cannot build the model.
public class Left
{
    public string Name { get; set; } = "";
}

public class Right
{
    public string Name { get; set; } = "";
}

public class BrokenDb : DbContext
{
    public BrokenDb(DbContextOptions<BrokenDb> options)
        : base(options)
    {
    }

    public DbSet<Left> Lefts => Set<Left>();

    public DbSet<Right> Rights => Set<Right>();
}
