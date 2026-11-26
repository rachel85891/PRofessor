using Microsoft.EntityFrameworkCore;
using SplitModels;

namespace SplitData;

public class ShopDb : DbContext
{
    public ShopDb(DbContextOptions<ShopDb> options)
        : base(options)
    {
    }

    public DbSet<Item> Items => Set<Item>();
}

public class ShopRepository
{
    private readonly ShopDb _db;

    public ShopRepository(ShopDb db) => _db = db;

    public Item Add(Item item)
    {
        _db.Items.Add(item);
        _db.SaveChanges();
        return item;
    }

    public List<Item> All() => _db.Items.ToList();
}
