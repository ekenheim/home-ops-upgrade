import marimo

__generated_with = "0.20.2"
app = marimo.App(width="medium", title="Bayesian Bandits — Thompson Sampling Prototype")


@app.cell
def _():
    import marimo as mo
    return (mo,)


@app.cell
def _(mo):
    mo.md(
        r"""
        # Bayesian Bandits — Thompson Sampling Prototype

        This notebook prototypes the Bayesian bandit model that will run in production via
        Ray Serve (hot path) and PyMC on Ray via Dagster (offline posterior refit).

        **Stack context:**
        - Arms (content variants) are defined in Strapi CMS
        - Alpha/beta counts live in DragonflyDB (Redis-compatible)
        - Periodic Bayesian refit runs on Ray workers (PyMC + arviz pre-installed)
        - Experiment tracking goes to MLflow

        The conjugate Beta-Bernoulli model is the production hot path.
        The PyMC section below validates the offline refit that Dagster schedules.
        """
    )
    return


@app.cell
def _():
    import numpy as np
    import pymc as pm
    import arviz as az
    import matplotlib.pyplot as plt
    from scipy.stats import beta as beta_dist
    return az, beta_dist, np, plt, pm


@app.cell
def _(mo):
    mo.md("## Simulation parameters")
    return


@app.cell
def _(mo):
    n_arms_slider = mo.ui.slider(2, 8, value=4, label="Number of arms")
    n_rounds_slider = mo.ui.slider(100, 5000, value=1000, step=100, label="Rounds to simulate")
    mo.hstack([n_arms_slider, n_rounds_slider])
    return n_arms_slider, n_rounds_slider


@app.cell
def _(mo, n_arms_slider, n_rounds_slider, np):
    n_arms = n_arms_slider.value
    n_rounds = n_rounds_slider.value

    # True reward probabilities — unknown to the bandit, used only to generate simulated rewards
    rng = np.random.default_rng(42)
    true_probs = np.sort(rng.uniform(0.05, 0.6, size=n_arms))
    best_arm = int(np.argmax(true_probs))

    mo.md(
        f"**True arm probabilities (hidden from bandit):** "
        + ", ".join(f"Arm {i}: {p:.3f}" for i, p in enumerate(true_probs))
        + f"\n\n**Best arm:** {best_arm} (p={true_probs[best_arm]:.3f})"
    )
    return best_arm, n_arms, n_rounds, rng, true_probs


@app.cell
def _(mo):
    mo.md("## Thompson Sampling simulation (conjugate Beta-Bernoulli)")
    return


@app.cell
def _(best_arm, n_arms, n_rounds, np, rng, true_probs):
    # Beta(alpha, beta) posterior params — start with uninformative Beta(1,1) prior
    alpha = np.ones(n_arms)
    beta = np.ones(n_arms)

    selections = []
    rewards = []
    cumulative_regret = [0.0]

    for _ in range(n_rounds):
        # Thompson sample: draw from each arm's posterior and pick the max
        samples = rng.beta(alpha, beta)
        chosen = int(np.argmax(samples))
        reward = int(rng.random() < true_probs[chosen])

        # Conjugate update
        if reward:
            alpha[chosen] += 1
        else:
            beta[chosen] += 1

        selections.append(chosen)
        rewards.append(reward)
        # Regret = best arm probability - chosen arm probability
        cumulative_regret.append(
            cumulative_regret[-1] + (true_probs[best_arm] - true_probs[chosen])
        )

    arm_counts = [selections.count(i) for i in range(n_arms)]
    return (
        alpha,
        arm_counts,
        beta,
        chosen,
        cumulative_regret,
        reward,
        rewards,
        selections,
    )


@app.cell
def _(alpha, arm_counts, beta, best_arm, cumulative_regret, mo, n_arms, n_rounds, np, plt, true_probs):
    fig, axes = plt.subplots(1, 3, figsize=(16, 4))

    # --- Plot 1: Posterior Beta distributions per arm ---
    x = np.linspace(0, 1, 300)
    from scipy.stats import beta as beta_dist_plot
    colors = plt.cm.tab10.colors
    for i in range(n_arms):
        y = beta_dist_plot.pdf(x, alpha[i], beta[i])
        axes[0].plot(x, y, color=colors[i], label=f"Arm {i} (n={arm_counts[i]})", linewidth=2)
        axes[0].axvline(true_probs[i], color=colors[i], linestyle="--", alpha=0.5)
    axes[0].axvline(true_probs[best_arm], color="black", linestyle=":", linewidth=2, label="Best arm true p")
    axes[0].set_title("Posterior Beta distributions after simulation")
    axes[0].set_xlabel("Reward probability p")
    axes[0].set_ylabel("Density")
    axes[0].legend(fontsize=8)

    # --- Plot 2: Arm selection frequency ---
    axes[1].bar(range(n_arms), arm_counts, color=[colors[i] for i in range(n_arms)])
    axes[1].axhline(n_rounds / n_arms, color="gray", linestyle="--", label="Uniform baseline")
    axes[1].set_title("Arm selection counts")
    axes[1].set_xlabel("Arm")
    axes[1].set_ylabel("Times selected")
    axes[1].set_xticks(range(n_arms))
    axes[1].legend()

    # --- Plot 3: Cumulative regret ---
    axes[2].plot(cumulative_regret, color="crimson", linewidth=1.5)
    axes[2].set_title("Cumulative regret over time")
    axes[2].set_xlabel("Round")
    axes[2].set_ylabel("Cumulative regret")

    fig.suptitle(
        f"Thompson Sampling — {n_arms} arms, {n_rounds} rounds  |  "
        f"Best arm {best_arm} selected {arm_counts[best_arm]/n_rounds*100:.1f}% of time",
        fontsize=12,
    )
    plt.tight_layout()
    mo.pyplot(fig)
    return axes, colors, fig, x


@app.cell
def _(alpha, arm_counts, beta, mo, n_arms, np, true_probs):
    posterior_means = alpha / (alpha + beta)
    rows = [
        {
            "Arm": i,
            "True p": f"{true_probs[i]:.3f}",
            "Posterior mean": f"{posterior_means[i]:.3f}",
            "α": int(alpha[i]),
            "β": int(beta[i]),
            "Selected": arm_counts[i],
        }
        for i in range(n_arms)
    ]
    mo.ui.table(rows)
    return posterior_means, rows


@app.cell
def _(mo):
    mo.md(
        r"""
        ## PyMC offline refit (validates the Dagster scheduled job)

        In production, Dagster reads the reward log from Postgres and submits a RayJob
        that refits Beta(α, β) posteriors using PyMC. This is equivalent to the conjugate
        update above but allows for more expressive models (informative priors, hierarchical
        pooling across experiments, non-conjugate likelihoods).

        The cell below validates that PyMC recovers the same posteriors as the analytic update.
        """
    )
    return


@app.cell
def _(mo):
    run_pymc = mo.ui.run_button(label="Run PyMC refit (slow — uses MCMC)")
    run_pymc
    return (run_pymc,)


@app.cell
def _(alpha, az, beta, mo, n_arms, np, pm, run_pymc):
    mo.stop(not run_pymc.value, mo.md("*Click the button above to run the PyMC refit.*"))

    # Reconstruct observed successes and trials from the Beta posterior params
    # alpha = 1 + successes, beta = 1 + failures (with Beta(1,1) prior)
    successes = (alpha - 1).astype(int)
    trials = (alpha + beta - 2).astype(int)

    with pm.Model() as bandit_model:
        # Weakly informative Beta(2,2) prior — slight pull toward 0.5
        p = pm.Beta("p", alpha=2, beta=2, shape=n_arms)
        obs = pm.Binomial("obs", n=trials, p=p, observed=successes)
        trace = pm.sample(500, tune=500, chains=2, progressbar=False, random_seed=0)

    # Compare PyMC posterior means to analytic posterior means
    pymc_means = trace.posterior["p"].mean(dim=["chain", "draw"]).values
    analytic_means = alpha / (alpha + beta)

    summary_data = [
        {
            "Arm": i,
            "Analytic posterior mean": f"{analytic_means[i]:.4f}",
            "PyMC posterior mean": f"{pymc_means[i]:.4f}",
            "Δ": f"{abs(pymc_means[i] - analytic_means[i]):.4f}",
        }
        for i in range(n_arms)
    ]

    mo.vstack([
        mo.md("### PyMC vs analytic posterior means"),
        mo.ui.table(summary_data),
        mo.md(
            f"Max absolute difference: **{max(abs(pymc_means[i] - analytic_means[i]) for i in range(n_arms)):.4f}**  \n"
            "Small differences are expected due to MCMC sampling noise and the slightly informative Beta(2,2) prior."
        ),
    ])
    return (
        analytic_means,
        bandit_model,
        obs,
        p,
        pymc_means,
        successes,
        summary_data,
        trace,
        trials,
    )


@app.cell
def _(mo):
    mo.md(
        r"""
        ## DragonflyDB state schema (production reference)

        In production, the Ray Serve bandit app reads/writes arm state from DragonflyDB
        (`dragonfly-master.database.svc.cluster.local:6379`) using Redis hash commands:

        ```
        # Read all arms for an experiment
        HGETALL bandit:experiment_id:arms

        # Read a single arm's alpha/beta
        HMGET bandit:experiment_id:arm_id alpha beta

        # Increment alpha on reward=1
        HINCRBY bandit:experiment_id:arm_id alpha 1

        # Increment beta on reward=0
        HINCRBY bandit:experiment_id:arm_id beta 1
        ```

        The Dagster pipeline writes the updated PyMC posterior means back as:
        ```
        HMSET bandit:experiment_id:arm_id alpha <new_alpha> beta <new_beta>
        ```

        MLflow run logs: `alpha_arm_0`, `beta_arm_0`, `mean_arm_0`, `n_rewards_arm_0`, …
        """
    )
    return


if __name__ == "__main__":
    app.run()
