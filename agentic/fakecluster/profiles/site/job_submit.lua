-- hpc-bridge fake cluster, `site` profile: a job on the gpu partition must request a GPU (the rule NCSA Delta's
-- GPU partitions enforce — the plugin's discovery/gate must surface it rather than let the block PEND or fail).
function slurm_job_submit(job_desc, part_list, submit_uid)
  if job_desc.partition == "gpu" then
    local tres = job_desc.tres_per_node or job_desc.tres_per_job or job_desc.tres_per_task or ""
    if not string.find(tres, "gpu") then
      slurm.log_user("site rule: jobs on the gpu partition must request a GPU (e.g. --gpus-per-node=1)")
      return slurm.ERROR
    end
  end
  return slurm.SUCCESS
end

function slurm_job_modify(job_desc, job_rec, part_list, modify_uid)
  return slurm.SUCCESS
end
