import sqlite3
conn = sqlite3.connect('veritas.db')
c = conn.cursor()

# Check targets
c.execute('SELECT * FROM targets LIMIT 10')
print("TARGETS:")
for row in c.fetchall():
    print(row)

print("\nINVENTORY:")
c.execute('SELECT * FROM inventory LIMIT 10')
for row in c.fetchall():
    print(row)

print("\nWALKER_STATE:")
c.execute('SELECT * FROM walker_state LIMIT 10')
for row in c.fetchall():
    print(row)