-- EXPECT: 0
-- Negative exponent: integer luai_ipow returns 0
local a, b = 2, -3
return a ^ b
