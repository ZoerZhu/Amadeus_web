export const divergenceValues = [
  "0.000000",
  "0.134891",
  "0.170922",
  "0.210317",
  "0.295582",
  "0.328403",
  "0.334581",
  "0.337161",
  "0.337187",
  "0.337199",
  "0.337337",
  "0.409420",
  "0.409431",
  "0.456903",
  "0.456914",
  "0.456923",
  "0.509736",
  "0.523299",
  "0.523307",
  "0.549111",
  "0.571015",
  "0.571024",
  "0.571046",
  "0.571082",
  "0.615483",
  "0.751354",
  "0.815524",
  "0.934587",
  "1.048264",
  "1.048596",
  "1.048599",
  "1.048728",
  "1.049326",
  "1.053649",
  "1.055821",
  "1.064750",
  "1.064756",
  "1.081163",
  "1.097302",
  "1.123581",
  "1.129848",
  "1.129954",
  "1.130205",
  "1.130206",
  "1.130207",
  "1.130208",
  "1.130209",
  "1.130211",
  "1.130212",
  "1.130238",
  "1.130426",
  "1.143688",
  "1.382733",
  "1.467093",
  "1.818520",
  "2.224529",
  "2.615074",
  "3.019430",
  "3.030493",
  "3.130238",
  "3.182879",
  "3.372329",
  "3.386019",
  "3.406288",
  "3.600104",
  "3.667293",
  "4.389117",
  "4.456441",
  "4.456442",
  "4.493623",
  "4.493624",
  "4.530805",
  "4.530806",
] as const;

export const highWeightDivergenceValues = [
  "1.048596",
  "0.571024",
  "1.130426",
  "1.129848",
  "1.097302",
  "1.123581",
] as const;

const HIGH_WEIGHT_PROBABILITY = 0.1;
const highWeightSet = new Set<string>(highWeightDivergenceValues);
const secondaryDivergenceValues = divergenceValues.filter((value) => !highWeightSet.has(value));

export function pickWeightedDivergenceValue(random = Math.random) {
  const highWeightTotal = highWeightDivergenceValues.length * HIGH_WEIGHT_PROBABILITY;
  const roll = random();

  if (roll < highWeightTotal) {
    const index = Math.min(
      Math.floor(roll / HIGH_WEIGHT_PROBABILITY),
      highWeightDivergenceValues.length - 1
    );
    return highWeightDivergenceValues[index];
  }

  const secondaryRoll = (roll - highWeightTotal) / (1 - highWeightTotal);
  const secondaryIndex = Math.min(
    Math.floor(secondaryRoll * secondaryDivergenceValues.length),
    secondaryDivergenceValues.length - 1
  );
  return secondaryDivergenceValues[secondaryIndex] ?? "1.048596";
}
