# The role CI releases this environment with (deploy/aws/release.sh from the workflows), taken
# on by a job of a workflow on main through GitHub's OpenID Connect provider, which the account
# root makes. It can run a command on the instance, which is root there, so that is all GitHub
# may do with it: find the instance, have Systems Manager run the release, follow it, record
# the tag, and read where the app answers. Another environment than staging also reads
# staging's release history, since it takes only a tag staging ran.

locals {
  github_oidc = "arn:${local.partition}:iam::${local.account}:oidc-provider/token.actions.githubusercontent.com"

  release_trust = {
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRoleWithWebIdentity"
      Principal = { Federated = local.github_oidc }
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
          "token.actions.githubusercontent.com:sub" = "repo:${var.github_repository}:ref:refs/heads/main"
        }
      }
    }]
  }

  release_policy = {
    Version = "2012-10-17"
    Statement = concat(
      [
        {
          Sid      = "FindTheInstance"
          Effect   = "Allow"
          Action   = "ec2:DescribeInstances"
          Resource = "*"
        },
        {
          Sid    = "RunTheRelease"
          Effect = "Allow"
          Action = "ssm:SendCommand"
          Resource = [
            aws_instance.app.arn,
            "arn:${local.partition}:ssm:${local.region}::document/AWS-RunShellScript",
          ]
        },
        {
          Sid      = "FollowTheRelease"
          Effect   = "Allow"
          Action   = "ssm:GetCommandInvocation"
          Resource = "*"
        },
        {
          Sid      = "RecordTheRelease"
          Effect   = "Allow"
          Action   = ["ssm:GetParameter", "ssm:PutParameter"]
          Resource = "${local.parameters_arn}/release"
        },
        {
          Sid      = "FindTheApp"
          Effect   = "Allow"
          Action   = "ssm:GetParameter"
          Resource = "${local.parameters_arn}/settings/APP_URL"
        },
      ],
      var.environment == "staging" ? [] : [{
        Sid      = "CheckThatStagingRanIt"
        Effect   = "Allow"
        Action   = "ssm:GetParameterHistory"
        Resource = "arn:${local.partition}:ssm:${local.region}:${local.account}:parameter/emudoc/staging/release"
      }],
    )
  }
}

resource "aws_iam_role" "release" {
  count = var.github_repository == "" ? 0 : 1

  name                 = "${local.name}-github-release"
  assume_role_policy   = jsonencode(local.release_trust)
  max_session_duration = 3600
}

resource "aws_iam_role_policy" "release" {
  count = var.github_repository == "" ? 0 : 1

  name   = "release"
  role   = aws_iam_role.release[0].id
  policy = jsonencode(local.release_policy)
}
