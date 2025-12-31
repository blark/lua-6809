-- EXPECT: 55
local n, sum = 10, 0
while n > 0 do
  sum = sum + n
  n = n - 1
end
return sum
