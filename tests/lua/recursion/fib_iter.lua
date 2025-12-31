-- EXPECT: 6765
-- Iterative fibonacci (avoids multiplication)
local function fib(n)
  local a, b = 0, 1
  for i = 1, n do
    a, b = b, a + b
  end
  return a
end
return fib(20)
