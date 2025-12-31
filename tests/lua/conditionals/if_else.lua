-- EXPECT: 2
local x = 42
if x > 100 then
  return 1
elseif x > 40 then
  return 2
else
  return 3
end
