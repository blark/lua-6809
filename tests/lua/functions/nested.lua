-- EXPECT: 14
local function a(n) return n + 1 end
local function b(n) return a(a(n)) end
local function c(n) return b(b(n)) end
return c(10)
