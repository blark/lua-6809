-- EXPECT: 30
local function swap(a, b)
  return b, a
end
local x, y = swap(10, 20)
return x + y
