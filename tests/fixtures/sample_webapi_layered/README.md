# Layered sample Web API (synthetic)

A minimal two-project solution for the runner's integration tests: the Web project
`LayeredApi` references the class library `Repository`, which reads and writes the data
file `Repository/users.txt` through a hard-coded absolute Windows path. The tests edit the
path (to a relative one with `\`) or remove the data file. Synthetic; no student code.
