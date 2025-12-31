-- EXPECT: 1
local a = true
local b = false
if a and not b then
  return 1
else
  return 0
end
