# Controller semantics

Record the implemented equation before translating gains. Names such as `I`, `D`,
`Ti`, and `Td` alone are insufficient. Inspect firmware, block settings, or the
controller manual. Keep engineering units and actuator scaling explicit.

| Implementation | Meaning / conversion |
| --- | --- |
| Parallel continuous PID | `u = Kp*e + Ki*integral(e dt) + Kd*de/dt` |
| Ideal / standard PID | `u = Kp*(e + integral(e dt)/Ti + Td*de/dt)`; parallel `Ki=Kp/Ti`, `Kd=Kp*Td` for finite positive `Ti` |
| Time expressed in minutes | Convert `Ti` and `Td` to seconds before the above conversion |
| Discrete sum and difference | If `u=Kp*e + ci*sum(e) + cd*(e-e_prev)`, then `ci=Ki*Ts`, `cd=Kd/Ts`; only valid for this exact discretization |
| Velocity / incremental PID | Inspect the recurrence, stored state and saturation behavior; do not treat coefficients as positional gains |

`Ti=0` may mean disabled integration, invalid configuration, or something vendor-specific.
Do not divide by zero or assume its meaning. `Kp=0` with nonzero parallel `Ki` or `Kd`
cannot be represented by a finite ideal form. Reverse-action controllers may use
negative gains or invert the error externally; do not force all gains positive.

Check derivative on measurement versus error, derivative filter, setpoint weighting,
integral reset/anti-windup, bumpless transfer, sample interval, output bias, and PWM
or percentage scaling. Changing the setpoint can produce a derivative kick only in
some implementations. Changing `Ts` may require coefficient conversion.

For a custom expression, use its declared parameter names, dependencies, states,
bounds and equations. Do not relabel every coefficient `p/i/d`, execute untrusted
expressions, or assume that increasing a positive coefficient increases loop gain.
The bundled simulator supports only its documented parallel PID; use the user's
runtime for arbitrary expressions.

For cascaded control, identify which output drives which setpoint and observe both
loops. Normally establish the inner-loop response before evaluating the outer loop;
respect an already verified inner loop. Freeze unrelated parameters for attribution.
For independent loops, maintain separate experiments. Store all loop gains together
when rollback must restore a coupled configuration. A single output trace cannot
prove both loops' stability or justify giving them identical gains.
