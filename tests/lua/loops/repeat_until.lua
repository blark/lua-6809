-- EXPECT: 10
local x = 0
repeat
  x = x + 1
until x >= 10
return x
