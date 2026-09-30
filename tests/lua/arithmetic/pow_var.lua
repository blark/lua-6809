-- EXPECT: 1024
-- Power with variable operands: luai_ipow(long, long) at run time (host luac
-- folds constant 2^10, so this must not be a constant expression)
local a, b = 2, 10
return a ^ b
