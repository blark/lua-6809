-- EXPECT: 6
local function gcd(a, b)
  while b ~= 0 do
    a, b = b, a % b
  end
  return a
end
return gcd(48, 18)
