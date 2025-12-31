-- EXPECT: -2147483648
-- Max int32 + 1 overflows to min int32
local x = 2147483647
return x + 1
