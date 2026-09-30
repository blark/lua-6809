-- EXPECT: 65536
-- Power with operands read from a table
local t = {4, 8}
return t[1] ^ t[2]
