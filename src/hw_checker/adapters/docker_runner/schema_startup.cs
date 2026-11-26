// Added by the HW checker to the run's temporary copy only; never part of the student's repo.
#nullable disable
#pragma warning disable
[assembly: Microsoft.AspNetCore.Hosting.HostingStartup(typeof(HwCheckerSchema.SchemaStartup))]

namespace HwCheckerSchema
{
    public sealed class SchemaStartup : Microsoft.AspNetCore.Hosting.IHostingStartup
    {
        public void Configure(Microsoft.AspNetCore.Hosting.IWebHostBuilder builder)
        {
            builder.ConfigureServices(services =>
                Microsoft.Extensions.DependencyInjection.ServiceCollectionHostedServiceExtensions
                    .AddHostedService<SchemaService>(services));
        }
    }

    public sealed class NoUsableConstructor : System.Exception
    {
    }

    public sealed class SchemaService : Microsoft.Extensions.Hosting.IHostedService
    {
        private const string ContextType = "Microsoft.EntityFrameworkCore.DbContext";
        private const string EfAssembly = "Microsoft.EntityFrameworkCore";
        private const string Marker = "__MARKER__";
        private static readonly string[] FrameworkPrefixes =
            { "System.", "Microsoft.", "Azure.", "Newtonsoft.", "Swashbuckle.", "Humanizer" };

        private readonly System.IServiceProvider _services;

        public SchemaService(System.IServiceProvider services)
        {
            _services = services;
        }

        public System.Threading.Tasks.Task StartAsync(System.Threading.CancellationToken token)
        {
            string outcome;
            try
            {
                outcome = Run();
            }
            catch (System.Exception error)
            {
                outcome = "failed " + Describe(error);
            }
            System.Console.Out.WriteLine(Marker + outcome);
            System.Console.Out.Flush();
            return System.Threading.Tasks.Task.CompletedTask;
        }

        public System.Threading.Tasks.Task StopAsync(System.Threading.CancellationToken token)
        {
            return System.Threading.Tasks.Task.CompletedTask;
        }

        private string Run()
        {
            var contexts = FindContexts();
            if (contexts.Count == 0)
            {
                return "no_context";
            }
            var migrated = false;
            string failure = null;
            foreach (var type in contexts)
            {
                try
                {
                    migrated |= Create(type);
                }
                catch (System.Exception error)
                {
                    failure = failure ?? Describe(error);
                }
            }
            if (failure != null)
            {
                return "failed " + failure;
            }
            return migrated ? "migrated" : "created";
        }

        private static string Describe(System.Exception error)
        {
            while ((error is System.Reflection.TargetInvocationException
                    || error is System.AggregateException)
                   && error.InnerException != null)
            {
                error = error.InnerException;
            }
            return error.GetType().FullName ?? error.GetType().Name;
        }

        private System.Collections.Generic.List<System.Type> FindContexts()
        {
            var found = new System.Collections.Generic.List<System.Type>();
            foreach (var assembly in CandidateAssemblies())
            {
                foreach (var type in TypesOf(assembly))
                {
                    if (!type.IsAbstract && !type.IsGenericTypeDefinition && DerivesFromContext(type))
                    {
                        found.Add(type);
                    }
                }
            }
            return found;
        }

        private static System.Collections.Generic.IEnumerable<System.Type> TypesOf(
            System.Reflection.Assembly assembly)
        {
            try
            {
                return assembly.GetTypes();
            }
            catch (System.Reflection.ReflectionTypeLoadException error)
            {
                var loaded = new System.Collections.Generic.List<System.Type>();
                foreach (var type in error.Types)
                {
                    if (type != null)
                    {
                        loaded.Add(type);
                    }
                }
                return loaded;
            }
            catch (System.Exception)
            {
                return new System.Type[0];
            }
        }

        private static bool DerivesFromContext(System.Type type)
        {
            for (var parent = type.BaseType; parent != null; parent = parent.BaseType)
            {
                if (parent.FullName == ContextType)
                {
                    return true;
                }
            }
            return false;
        }

        private static System.Collections.Generic.List<System.Reflection.Assembly> CandidateAssemblies()
        {
            var assemblies = new System.Collections.Generic.List<System.Reflection.Assembly>();
            foreach (var path in System.IO.Directory.GetFiles(
                         System.AppContext.BaseDirectory, "*.dll"))
            {
                var assembly = TryLoad(path);
                if (assembly != null && UsesEntityFramework(assembly))
                {
                    assemblies.Add(assembly);
                }
            }
            return assemblies;
        }

        private static System.Reflection.Assembly TryLoad(string path)
        {
            try
            {
                var name = System.Reflection.AssemblyName.GetAssemblyName(path);
                foreach (var prefix in FrameworkPrefixes)
                {
                    if (name.Name.StartsWith(prefix, System.StringComparison.Ordinal))
                    {
                        return null;
                    }
                }
                return System.Reflection.Assembly.Load(name);
            }
            catch (System.Exception)
            {
                return null;
            }
        }

        private static bool UsesEntityFramework(System.Reflection.Assembly assembly)
        {
            foreach (var reference in assembly.GetReferencedAssemblies())
            {
                if (reference.Name == EfAssembly)
                {
                    return true;
                }
            }
            return false;
        }

        private bool Create(System.Type type)
        {
            using (var scope =
                   Microsoft.Extensions.DependencyInjection.ServiceProviderServiceExtensions
                       .CreateScope(_services))
            {
                var context = scope.ServiceProvider.GetService(type);
                var own = false;
                if (context == null)
                {
                    context = Construct(type);
                    own = true;
                }
                try
                {
                    return Ensure(context);
                }
                finally
                {
                    if (own && context is System.IDisposable disposable)
                    {
                        disposable.Dispose();
                    }
                }
            }
        }

        private static object Construct(System.Type type)
        {
            if (type.GetConstructor(System.Type.EmptyTypes) != null)
            {
                return System.Activator.CreateInstance(type);
            }
            foreach (var constructor in type.GetConstructors())
            {
                var parameters = constructor.GetParameters();
                if (parameters.Length == 1 && parameters[0].ParameterType.FullName != null
                    && parameters[0].ParameterType.FullName.StartsWith(
                        "Microsoft.EntityFrameworkCore.DbContextOptions",
                        System.StringComparison.Ordinal))
                {
                    var generic = parameters[0].ParameterType.Assembly
                        .GetType("Microsoft.EntityFrameworkCore.DbContextOptions`1")
                        .MakeGenericType(type);
                    return constructor.Invoke(new object[] { System.Activator.CreateInstance(generic) });
                }
            }
            throw new NoUsableConstructor();
        }

        private static bool Ensure(object context)
        {
            var database = context.GetType().GetProperty("Database").GetValue(context);
            if (HasMigrations(database))
            {
                RelationalMethod("Migrate", database).Invoke(null, new object[] { database });
                return true;
            }
            database.GetType().GetMethod("EnsureCreated", System.Type.EmptyTypes).Invoke(database, null);
            return false;
        }

        private static bool HasMigrations(object database)
        {
            try
            {
                var migrations = (System.Collections.IEnumerable)RelationalMethod("GetMigrations", database)
                    .Invoke(null, new object[] { database });
                return migrations.GetEnumerator().MoveNext();
            }
            catch (System.Exception)
            {
                return false;
            }
        }

        private static System.Reflection.MethodInfo RelationalMethod(string name, object database)
        {
            var relational = System.Reflection.Assembly.Load("Microsoft.EntityFrameworkCore.Relational");
            return relational.GetType("Microsoft.EntityFrameworkCore.RelationalDatabaseFacadeExtensions")
                .GetMethod(name, new[] { database.GetType() });
        }
    }
}
